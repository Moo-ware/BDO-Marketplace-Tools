import asyncio
import json
import random
import stat

import requests

from bdo_marketplace_tools.market.decoder import unpack
from bdo_marketplace_tools.ui.display import highlight, highlight_silver
from bdo_marketplace_tools.storage.app_settings import (
    PA_CREDENTIALS_MODE,
    STEAM_BROWSER_MODE,
    load_account_mode,
    save_saved_session_last_known_valid,
)
from bdo_marketplace_tools.storage.paths import SESSION_COOKIE_PATH


REQUEST_TIMEOUT = (5, 20)
TRADE_URL = "https://na-trade.naeu.playblackdesert.com"
GAME_TRADE_URL = "https://na-game-trade.naeu.playblackdesert.com"
MARKET_COOKIE_HOSTS = (
    "na-trade.naeu.playblackdesert.com",
    "na-game-trade.naeu.playblackdesert.com",
)
MARKET_COOKIE_PARENT_DOMAINS = ("naeu.playblackdesert.com",)
FORM_CONTENT_TYPE = "application/x-www-form-urlencoded; charset=UTF-8"
PUBLIC_MARKET_HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "BlackDesert",
}
MARKET_ACCEPT_LANGUAGE = "en-US,en;q=0.9,ko;q=0.8,zh-CN;q=0.7,zh;q=0.6"
MARKET_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/148.0.0.0 Safari/537.36"
)
MARKET_AJAX_HEADER = "XMLHttpRequest"
DEFAULT_PURCHASE_DELAY_BOUNDS = (1.0, 2.5)
OUTFIT_BOX_SCAN_CATEGORIES = (
    ("male", "male outfit stock", 1),
    ("female", "female outfit stock", 2),
)
OUTFIT_PIECE_SCAN_CATEGORIES = (
    ("male_pieces", "male outfit pieces stock", 3),
    ("female_pieces", "female outfit pieces stock", 4),
)


class MarketplaceAPIError(RuntimeError):
    pass


class MarketplaceNetworkError(MarketplaceAPIError):
    pass


class MarketplaceResponseError(MarketplaceAPIError):
    pass


PURCHASE_RESULT_REASONS = {
    30: "an identical order already exists",
    34: "item was unavailable or would create a duplicate pre-order",
    -14: "price mismatch",
    -16: "not enough silver for the order",
    2000: "login session expired",
}


def _format_silver_amount(price):
    try:
        return f"{int(price):,}"
    except (TypeError, ValueError):
        return str(price)


def _item_and_price(item_id, price):
    return f"{highlight(item_id)} at {highlight_silver(_format_silver_amount(price))} silver"


def purchase_result_message(result_code, item_id, price):
    if result_code == 0:
        return f"Purchase request succeeded for {_item_and_price(item_id, price)}."

    reason = PURCHASE_RESULT_REASONS.get(result_code)
    if reason:
        return f"Purchase failed for {_item_and_price(item_id, price)}: {reason}."
    return f"Purchase failed for {_item_and_price(item_id, price)}: resultCode {result_code}."


def purchase_success_message(item_id, actual_price, submitted_price=None):
    if submitted_price is not None and int(submitted_price) != int(actual_price):
        # The submitted buyPrice is a ceiling; the marketplace fills at the real
        # listing price. "Bid up to" tells the reader the ceiling was not what was paid.
        return (
            f"Purchase request succeeded for {_item_and_price(item_id, actual_price)} "
            f"(bid up to {_format_silver_amount(submitted_price)})."
        )
    return purchase_result_message(0, item_id, actual_price)


def purchase_preorder_message(item_id, price):
    return f"Purchase request placed a pre-order for {_item_and_price(item_id, price)}; no stock was bought."


def marketplace_silver_balance(wallet_response):
    if not isinstance(wallet_response, dict):
        raise MarketplaceResponseError("marketplace wallet lookup returned an unexpected JSON shape")

    wallet_items = wallet_response.get("myWalletList", [])
    if wallet_items is None:
        wallet_items = []
    if not isinstance(wallet_items, list):
        raise MarketplaceResponseError("marketplace wallet lookup returned an invalid myWalletList")

    for item in wallet_items:
        if not isinstance(item, dict):
            continue
        if item.get("mainKey") == 1 and item.get("subKey") == 0 and item.get("name") == "Silver":
            try:
                return int(item["count"])
            except (KeyError, TypeError, ValueError) as exc:
                raise MarketplaceResponseError("marketplace wallet silver row had an invalid count") from exc

    return None


class APIHandler:
    def __init__(self):
        self.trade_url = TRADE_URL
        self.game_trade_url = GAME_TRADE_URL
        self.session = requests.Session()
        self.public_market_sessions = {
            "male": requests.Session(),
            "female": requests.Session(),
            "male_pieces": requests.Session(),
            "female_pieces": requests.Session(),
        }
        self.login_status = False
        self.account_mode = load_account_mode()
        self.email = None
        self.password = None
        self._session_lock = asyncio.Lock()

        self.load_session()

    def _trade_url(self):
        return getattr(self, "trade_url", TRADE_URL).rstrip("/")

    def _game_trade_url(self):
        return getattr(self, "game_trade_url", GAME_TRADE_URL).rstrip("/")

    def _public_market_client(self, category_key):
        sessions = getattr(self, "public_market_sessions", None)
        if not sessions:
            return requests
        return sessions[category_key]

    def _has_session_cookies(self):
        session = getattr(self, "session", None)
        return bool(getattr(session, "cookies", None))

    def has_session_cookies(self):
        return self._has_session_cookies()

    def uses_browser_session(self):
        return getattr(self, "account_mode", PA_CREDENTIALS_MODE) == STEAM_BROWSER_MODE

    def _market_headers(self, referer=None, *, origin=None, content_type=FORM_CONTENT_TYPE, ajax=False):
        headers = {
            "Accept": "*/*",
            "Accept-Language": MARKET_ACCEPT_LANGUAGE,
            "User-Agent": MARKET_USER_AGENT,
        }
        if content_type is not None:
            headers["Content-Type"] = content_type
        if origin is None:
            origin = self._trade_url()
        if origin:
            headers["Origin"] = origin
        if referer:
            headers["Referer"] = referer
        if ajax:
            headers["X-Requested-With"] = MARKET_AJAX_HEADER
        return headers

    async def _request(self, client, method, url, context, **kwargs):
        request_kwargs = dict(kwargs)
        request_kwargs.setdefault("timeout", REQUEST_TIMEOUT)

        def send_request():
            try:
                response = client.request(method, url, **request_kwargs)
                response.raise_for_status()
                return response
            except requests.Timeout as exc:
                raise MarketplaceNetworkError(f"{context} timed out") from exc
            except requests.RequestException as exc:
                raise MarketplaceNetworkError(f"{context} failed: {exc}") from exc

        return await asyncio.to_thread(send_request)

    async def _session_request(self, method, url, context, **kwargs):
        async with self._session_lock:
            return await self._request(self.session, method, url, context, **kwargs)

    def _json_response(self, response, context):
        try:
            data = response.json()
        except ValueError as exc:
            raise MarketplaceResponseError(f"{context} returned invalid JSON") from exc

        if not isinstance(data, dict):
            raise MarketplaceResponseError(f"{context} returned an unexpected JSON shape")
        return data

    async def check_stock(self, include_outfit_pieces=False):
        url = f"{self._trade_url()}/Trademarket/GetWorldMarketList"
        headers = dict(PUBLIC_MARKET_HEADERS)
        categories = OUTFIT_BOX_SCAN_CATEGORIES
        if include_outfit_pieces:
            categories += OUTFIT_PIECE_SCAN_CATEGORIES

        responses = await asyncio.gather(
            *(
                self._request(
                    self._public_market_client(category_key),
                    "POST",
                    url,
                    f"{context} check",
                    json={
                        "keyType": 0,
                        "mainCategory": 55,
                        "subCategory": subcategory,
                    },
                    headers=headers,
                )
                for category_key, context, subcategory in categories
            )
        )

        stock = []
        for response, (_category_key, context, _subcategory) in zip(responses, categories):
            stock.extend(self._parse_world_market_response(response.content, context))
        return stock

    def _parse_world_market_response(self, content, context):
        try:
            decoded = unpack(content)
        except Exception as exc:
            raise MarketplaceResponseError(f"{context} response could not be decoded") from exc

        buy_list = []
        for row in decoded.split("|"):
            if not row:
                continue

            parts = row.split("-", 4)
            if len(parts) < 4:
                raise MarketplaceResponseError(f"{context} row had an unexpected shape: {row}")

            item_id, stock, price = parts[0], parts[1], parts[3]
            try:
                stock_count = int(stock)
            except ValueError as exc:
                raise MarketplaceResponseError(f"{context} row had invalid stock: {row}") from exc

            if stock_count > 0:
                buy_list.append([item_id, str(stock_count), price])

        return buy_list

    async def login(self):
        self.login_status = False
        raise MarketplaceResponseError(
            "Pearl Abyss password login is no longer supported. Use browser session refresh."
        )

    async def ensure_session_valid(self):
        status = await self.is_session_expired()
        if status == 0:
            self.login_status = True
            return True

        self.login_status = False
        return False

    async def buy_item(self, buy_list, purchase_delay_bounds=None, *, on_purchase=None):
        # on_purchase: optional synchronous observer called with each purchase_record the
        # moment that item is secured (before the inter-buy delay). Cosmetic only — it is
        # exception-isolated and must never influence purchasing, retries, or timing.
        url = f"{self._game_trade_url()}/GameTradeMarket/BuyItem"
        headers = self._market_headers(
            f"{self._trade_url()}/",
            origin=self._trade_url(),
            ajax=False,
        )
        purchase_delay_bounds = self._purchase_delay_bounds(purchase_delay_bounds)
        purchase_attempts = self._purchase_attempts(buy_list)

        summary = {
            "attempted": 0,
            "purchased": 0,
            "events": [],
            "purchase_records": [],
            "results": [],
            "purchase_delay_bounds": purchase_delay_bounds,
            "auth_failed": False,
        }

        if not getattr(self, "login_status", False) or not self._has_session_cookies():
            try:
                session_valid = await self.ensure_session_valid()
            except MarketplaceAPIError as exc:
                if not isinstance(exc, MarketplaceNetworkError):
                    summary["auth_failed"] = True
                summary["events"].append({"level": "error", "message": f"Purchase aborted: {exc}"})
                return summary

            if not session_valid:
                summary["auth_failed"] = True
                message = "Purchase aborted: Pearl Abyss Account session is invalid. Refresh Session before buying."
                if self.uses_browser_session():
                    message = "Purchase aborted: Steam Account session is invalid. Refresh Session before buying."
                summary["events"].append(
                    {
                        "level": "error",
                        "message": message,
                    }
                )
                return summary

        attempt_index = 0
        retried_after_session_refresh = False
        while attempt_index < len(purchase_attempts):
            item_id, price = purchase_attempts[attempt_index]
            payload = {
                "buyMainKey": item_id,
                "buySubKey": "0",
                "buyKeyType": "0",
                "isWaitItem": "false",
                "otp": "",
                "retryBiddingNo": "",
                "buyPrice": price,
                "buyCount": "1",
                "buyChooseKey": "0",
            }
            summary["attempted"] += 1
            response = await self._session_request(
                "POST",
                url,
                "purchase request",
                headers=headers,
                data=payload,
            )
            response_json = self._json_response(response, "purchase request")
            result_code = self._purchase_result_code(response_json)
            result_details = self._purchase_result_details(response_json, item_id, price)
            if result_code != 0:
                result_details["outcome"] = "failed"
                result_details["count"] = 0

            result_record = {
                "item_id": result_details["item_id"],
                "price": result_details["price"],
                "submitted_price": result_details["submitted_price"],
                "count": result_details["count"],
                "result_code": result_code,
                "outcome": result_details["outcome"],
                "response": response_json,
            }
            if result_details["reservation_id"]:
                result_record["reservation_id"] = result_details["reservation_id"]
            summary["results"].append(result_record)

            if result_code == 0:
                if result_details["outcome"] != "purchase":
                    summary["events"].append(
                        {
                            "level": "warning",
                            "message": purchase_preorder_message(
                                result_details["item_id"],
                                result_details["price"],
                            ),
                        }
                    )
                    break

                summary["purchased"] += result_details["count"]
                purchase_record = {
                    "item_id": result_details["item_id"],
                    "price": result_details["price"],
                    "submitted_price": result_details["submitted_price"],
                    "count": result_details["count"],
                    "result_code": result_code,
                }
                summary["purchase_records"].append(purchase_record)
                if on_purchase is not None:
                    try:
                        on_purchase(purchase_record)
                    except Exception:
                        pass  # observer failures must never affect the purchase flow
                summary["events"].append(
                    {
                        "level": "success",
                        "message": purchase_success_message(
                            result_details["item_id"],
                            result_details["price"],
                            result_details["submitted_price"],
                        )
                    }
                )
                await self._sleep_before_next_purchase_attempt(
                    attempt_index,
                    purchase_attempts,
                    purchase_delay_bounds,
                )
                attempt_index += 1
                retried_after_session_refresh = False
                continue

            if result_code == 2000:
                if self.uses_browser_session():
                    self.login_status = False
                    summary["auth_failed"] = True
                    summary["events"].append(
                        {
                            "level": "error",
                            "message": "Login session expired. Refresh the Steam Account session before buying.",
                        }
                    )
                    return summary

                summary["events"].append(
                    {"level": "warning", "message": "Login session expired. Attempting to re-authenticate."}
                )
                if retried_after_session_refresh:
                    summary["auth_failed"] = True
                    summary["events"].append(
                        {"level": "error", "message": "Purchase aborted: session still expired after re-authentication."}
                    )
                    return summary

                try:
                    reauthenticated = await self.ensure_session_valid()
                except MarketplaceAPIError as exc:
                    if not isinstance(exc, MarketplaceNetworkError):
                        summary["auth_failed"] = True
                    summary["events"].append({"level": "error", "message": f"Re-authentication failed: {exc}"})
                    return summary

                if reauthenticated:
                    summary["events"].append({"level": "success", "message": "Re-authentication succeeded."})
                    retried_after_session_refresh = True
                    continue

                summary["auth_failed"] = True
                summary["events"].append({"level": "error", "message": "Re-authentication failed."})
                return summary

            summary["events"].append(
                {
                    "level": "warning",
                    "message": purchase_result_message(result_code, item_id, price),
                }
            )
            break

        return summary

    def _purchase_attempts(self, buy_list):
        attempts = []
        for item in buy_list:
            try:
                item_id, stock, price = item[0], item[1], item[2]
            except (IndexError, TypeError) as exc:
                raise MarketplaceResponseError(f"purchase row had an unexpected shape: {item}") from exc

            try:
                stock_count = int(stock)
            except (TypeError, ValueError) as exc:
                raise MarketplaceResponseError(f"purchase row had invalid stock: {item}") from exc

            for _ in range(max(0, stock_count)):
                attempts.append((str(item_id), str(price)))
        return attempts

    def _purchase_delay_bounds(self, purchase_delay_bounds):
        bounds = DEFAULT_PURCHASE_DELAY_BOUNDS if purchase_delay_bounds is None else purchase_delay_bounds
        try:
            low, high = bounds
            low = float(low)
            high = float(high)
        except (TypeError, ValueError) as exc:
            raise MarketplaceResponseError("purchase delay bounds must be a two-value range") from exc

        if low < 0 or high < 0 or low > high:
            raise MarketplaceResponseError(
                "purchase delay bounds must use non-negative seconds with min less than or equal to max"
            )
        return (low, high)

    async def _sleep_before_next_purchase_attempt(self, attempt_index, purchase_attempts, purchase_delay_bounds):
        if attempt_index >= len(purchase_attempts) - 1:
            return

        low, high = purchase_delay_bounds
        if high <= 0:
            return

        await asyncio.sleep(random.uniform(low, high))

    def _purchase_result_code(self, response_json):
        if "resultCode" not in response_json:
            raise MarketplaceResponseError("purchase response did not include resultCode")
        try:
            return int(response_json["resultCode"])
        except (TypeError, ValueError) as exc:
            raise MarketplaceResponseError("purchase response had an invalid resultCode") from exc

    def _purchase_result_details(self, response_json, item_id, submitted_price):
        submitted_price = int(submitted_price)
        details = {
            "item_id": str(item_id),
            "submitted_price": submitted_price,
            "price": submitted_price,
            "count": 1,
            "outcome": "purchase",
            "reservation_id": None,
        }

        result_msg = response_json.get("resultMsg")
        if not isinstance(result_msg, str) or not result_msg:
            return details

        row = result_msg.split("|", 1)[0]
        parts = row.split("-")
        if len(parts) < 6:
            return details

        try:
            details["item_id"] = parts[0] or details["item_id"]
            details["count"] = int(parts[4])
            details["price"] = int(parts[5])
        except (TypeError, ValueError):
            return details

        reservation_id = self._optional_positive_int(parts[7] if len(parts) > 7 else None)
        if reservation_id is not None:
            details["reservation_id"] = reservation_id

        if details["count"] <= 0:
            details["outcome"] = "preorder"
            details["count"] = 0

        return details

    def _optional_positive_int(self, value):
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        if parsed <= 0:
            return None
        return parsed

    async def is_session_expired(self):
        if not self.session.cookies:
            return -1

        url = f"{self._trade_url()}/Home/AppSessionRefresh"
        headers = self._market_headers(f"{self._trade_url()}/Home/list/hot", ajax=True)

        response = await self._session_request(
            "POST",
            url,
            "session refresh",
            headers=headers,
            data={"_isCalc": "false"},
        )
        response_json = self._json_response(response, "session refresh")
        result_code = self._session_refresh_result_code(response_json)
        return 0 if result_code == 0 else -1

    def _session_refresh_result_code(self, response_json):
        for key in ("_resultCode", "resultCode"):
            if key not in response_json:
                continue
            try:
                return int(response_json[key])
            except (TypeError, ValueError) as exc:
                raise MarketplaceResponseError(f"session refresh response had an invalid {key}") from exc

        raise MarketplaceResponseError("session refresh response did not include _resultCode or resultCode")

    def save_session(self, *, last_known_valid=None):
        SESSION_COOKIE_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "cookies": self._serialize_cookies(),
        }
        with SESSION_COOKIE_PATH.open("w", encoding="utf-8") as file:
            json.dump(payload, file, indent=2)
            file.write("\n")
        self._restrict_session_file_permissions()
        if last_known_valid is not None:
            save_saved_session_last_known_valid(bool(last_known_valid))

    def clear_session(self, save=True):
        self.session = requests.Session()
        self.login_status = False
        if save:
            self.save_session(last_known_valid=False)
        else:
            save_saved_session_last_known_valid(False)

    def _restrict_session_file_permissions(self):
        try:
            SESSION_COOKIE_PATH.chmod(stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass

    def _serialize_cookies(self):
        cookies = []
        for cookie in self.session.cookies:
            cookies.append(
                {
                    "name": cookie.name,
                    "value": cookie.value,
                    "domain": cookie.domain,
                    "path": cookie.path,
                    "secure": cookie.secure,
                    "expires": cookie.expires,
                }
            )
        return cookies

    def load_session(self):
        self.session = requests.Session()
        if SESSION_COOKIE_PATH.exists():
            try:
                with SESSION_COOKIE_PATH.open("r", encoding="utf-8-sig") as file:
                    payload = json.load(file)
                self._load_cookies(payload.get("cookies", []))
                return 0
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                pass

        if not SESSION_COOKIE_PATH.exists():
            self.save_session()
        return -1

    def _load_cookies(self, cookies):
        jar = requests.cookies.RequestsCookieJar()
        for cookie in cookies:
            name = cookie.get("name")
            value = cookie.get("value")
            if not name or value is None:
                continue

            kwargs = {
                "name": name,
                "value": value,
                "path": cookie.get("path") or "/",
                "secure": bool(cookie.get("secure")),
            }
            if cookie.get("domain"):
                kwargs["domain"] = cookie["domain"]
            if cookie.get("expires"):
                kwargs["expires"] = cookie["expires"]

            jar.set_cookie(requests.cookies.create_cookie(**kwargs))

        self.session.cookies.update(jar)

    async def validate_and_save_imported_session(self, cookies):
        previous_session = self.session
        previous_login_status = self.login_status
        new_session = self._session_from_browser_cookies(cookies)
        if not new_session.cookies:
            return False

        self.session = new_session
        try:
            status = await self.is_session_expired()
        except MarketplaceAPIError:
            self.session = previous_session
            self.login_status = previous_login_status
            raise

        if status == 0:
            self.login_status = True
            self.save_session(last_known_valid=True)
            return True

        self.session = previous_session
        self.login_status = previous_login_status
        return False

    def _session_from_browser_cookies(self, cookies):
        new_session = requests.Session()
        new_session.cookies.update(self._browser_cookie_jar(cookies))
        return new_session

    def _browser_cookie_jar(self, cookies):
        jar = requests.cookies.RequestsCookieJar()
        for cookie in cookies or []:
            normalized = self._normalize_browser_cookie(cookie)
            if normalized is None:
                continue
            jar.set_cookie(requests.cookies.create_cookie(**normalized))
        return jar

    def _normalize_browser_cookie(self, cookie):
        if isinstance(cookie, dict):
            data = cookie
        elif hasattr(cookie, "name") and hasattr(cookie, "value"):
            data = {
                "name": cookie.name,
                "value": cookie.value,
                "domain": cookie.domain,
                "path": cookie.path,
                "secure": cookie.secure,
                "expires": cookie.expires,
            }
        else:
            return None

        name = data.get("name")
        value = data.get("value")
        domain = data.get("domain")
        if not name or value is None or not domain or not self._is_market_cookie_domain(domain):
            return None

        normalized = {
            "name": name,
            "value": value,
            "domain": domain,
            "path": data.get("path") or "/",
            "secure": bool(data.get("secure")),
        }
        expires = self._normalize_browser_cookie_expires(data.get("expires"))
        if expires is not None:
            normalized["expires"] = expires
        return normalized

    def _normalize_browser_cookie_expires(self, expires):
        if expires in (None, "", -1):
            return None
        try:
            expires = int(float(expires))
        except (TypeError, ValueError):
            return None
        return expires if expires > 0 else None

    def _is_market_cookie_domain(self, domain):
        normalized = str(domain or "").lstrip(".").lower()
        if normalized in MARKET_COOKIE_HOSTS or normalized in MARKET_COOKIE_PARENT_DOMAINS:
            return True
        return False

    async def get_mp_inventory(self):
        headers = self._market_headers(f"{self._trade_url()}/Home/list/hot", ajax=True)
        response = await self._session_request(
            "POST",
            f"{self._trade_url()}/Home/GetMyWalletList",
            "marketplace wallet lookup",
            headers=headers,
        )
        return self._json_response(response, "marketplace wallet lookup")

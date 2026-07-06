"""Test-mode marketplace probes.

This module is intentionally separate from the production API handler. The
Seleth Longsword path exists to validate the monitor and buy pipeline against a
small live listing before switching the same architecture back to outfit rows.
"""

import requests

from bdo_marketplace_tools.market.api_handler import PUBLIC_MARKET_HEADERS, MarketplaceResponseError


SINGLE_ITEM_TEST_TARGET = {
    "name": "Seleth Longsword",
    "main_key": "10007",
    "key_type": 0,
    "enhance_min": "0",
    "enhance_max": "7",
    "sub_key": "0",
    "choose_key": "0",
    "main_category": "1",
    "sub_category": "1",
    "max_buy_price": "92500",
}

LIVE_BUY_ERROR_TEST_TARGET = {
    "name": "Live Buy Error Probe",
    "main_key": "15280",
    "stock": "1",
    "max_buy_price": "2900000000",
}


def live_buy_error_test_listing(target=None):
    target = target or LIVE_BUY_ERROR_TEST_TARGET
    return [[target["main_key"], target["stock"], target["max_buy_price"]]]


async def check_single_item_stock(api_handler, target=None):
    target = target or SINGLE_ITEM_TEST_TARGET
    context = f"{target['name']} public single-item stock check"
    url = f"{api_handler._trade_url()}/Trademarket/GetWorldMarketSubList"
    payload = {
        "keyType": int(target.get("key_type", 0)),
        "mainKey": int(target["main_key"]),
    }

    response = await api_handler._request(
        requests,
        "POST",
        url,
        context,
        json=payload,
        headers=dict(PUBLIC_MARKET_HEADERS),
    )
    response_json = api_handler._json_response(response, context)
    return parse_single_item_stock_response(response_json, target, context)


def parse_single_item_stock_response(response_json, target, context):
    try:
        result_code = int(response_json["resultCode"])
    except (KeyError, TypeError, ValueError) as exc:
        raise MarketplaceResponseError(f"{context} response did not include a valid resultCode") from exc

    if result_code != 0:
        result_msg = response_json.get("resultMsg") or f"resultCode {result_code}"
        raise MarketplaceResponseError(f"{context} failed: {result_msg}")

    result_msg = response_json.get("resultMsg", "")
    if not isinstance(result_msg, str):
        raise MarketplaceResponseError(f"{context} response did not include a valid resultMsg")

    expected_main_key = int(target["main_key"])
    expected_enhance_min = int(target.get("enhance_min", 0))
    expected_enhance_max = int(target.get("enhance_max", expected_enhance_min))

    for row in result_msg.split("|"):
        if not row:
            continue

        parts = row.split("-")
        if len(parts) <= 9:
            raise MarketplaceResponseError(f"{context} row had an unexpected shape: {row}")

        try:
            row_main_key = int(parts[0])
            row_enhance_min = int(parts[1])
            row_enhance_max = int(parts[2])
        except (TypeError, ValueError):
            continue

        if (
            row_main_key != expected_main_key
            or row_enhance_min != expected_enhance_min
            or row_enhance_max != expected_enhance_max
        ):
            continue

        try:
            stock_count = int(parts[4])
        except (TypeError, ValueError) as exc:
            raise MarketplaceResponseError(f"{context} target row had an invalid stock count") from exc

        max_price = target["max_buy_price"]
        try:
            int(max_price)
        except (TypeError, ValueError) as exc:
            raise MarketplaceResponseError(f"{context} target configuration had an invalid max buy price") from exc

        if stock_count <= 0:
            return []

        return [[target["main_key"], str(stock_count), max_price]]

    raise MarketplaceResponseError(f"{context} response did not include the target enhancement row")

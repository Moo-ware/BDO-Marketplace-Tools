import asyncio
import inspect
import re


AUTH_DIALOG_VERIFICATION_REQUIRED = "verification_required"
AUTH_DIALOG_INVALID_CREDENTIALS = "invalid_credentials"
AUTH_DIALOG_MANUAL_ATTENTION = "manual_attention"
AUTH_DIALOG_VERIFICATION_MARKERS = (
    "please complete the verification",
    "verification",
    "captcha",
)
AUTH_DIALOG_INVALID_CREDENTIAL_MARKERS = (
    "please double-check your email and password",
    "email and password",
    "double-check",
    "invalid",
    "password",
)


async def _maybe_await(value):
    if inspect.isawaitable(value):
        return await value
    return value


def _new_auth_dialog_state():
    return {
        "attached_pages": set(),
        "records": [],
        "manual_attention": None,
        "reported": set(),
    }


def _sanitize_dialog_message(message):
    message = "" if message is None else str(message)
    message = re.sub(r"[\w.+-]+@[\w.-]+", "[email]", message)
    message = re.sub(r"\s+", " ", message).strip()
    return message[:300]


def _classify_auth_dialog_message(message):
    normalized = _sanitize_dialog_message(message).lower()
    if not normalized:
        return None
    if any(marker in normalized for marker in AUTH_DIALOG_VERIFICATION_MARKERS):
        return AUTH_DIALOG_VERIFICATION_REQUIRED
    if any(marker in normalized for marker in AUTH_DIALOG_INVALID_CREDENTIAL_MARKERS):
        return AUTH_DIALOG_INVALID_CREDENTIALS
    return None


def _auth_dialog_status_message(category):
    if category == AUTH_DIALOG_VERIFICATION_REQUIRED:
        return "Pearl Abyss verification is required. Complete it manually in the browser."
    if category == AUTH_DIALOG_INVALID_CREDENTIALS:
        return "Pearl Abyss rejected the saved email/password. Update saved credentials before refreshing again."
    return "Pearl Abyss login needs manual attention. Complete login manually in the browser."


def _record_auth_dialog(dialog_state, message, dialog_type=None):
    sanitized = _sanitize_dialog_message(message)
    category = _classify_auth_dialog_message(sanitized)
    record = {
        "message": sanitized,
        "type": "" if dialog_type is None else str(dialog_type),
        "category": category or AUTH_DIALOG_MANUAL_ATTENTION,
    }
    dialog_state["records"].append(record)
    if category is not None:
        dialog_state["manual_attention"] = record
    return record


async def _accept_or_dismiss_dialog(dialog):
    accept = getattr(dialog, "accept", None)
    if callable(accept):
        try:
            await _maybe_await(accept())
            return
        except Exception:
            pass

    dismiss = getattr(dialog, "dismiss", None)
    if callable(dismiss):
        try:
            await _maybe_await(dismiss())
        except Exception:
            pass


async def _handle_auth_dialog(dialog, dialog_state):
    message = getattr(dialog, "message", "")
    if callable(message):
        try:
            message = message()
        except Exception:
            message = ""
    dialog_type = getattr(dialog, "type", "")
    if callable(dialog_type):
        try:
            dialog_type = dialog_type()
        except Exception:
            dialog_type = ""
    _record_auth_dialog(dialog_state, message, dialog_type)
    await _accept_or_dismiss_dialog(dialog)


def _install_auth_dialog_page_handler(page, dialog_state):
    if page is None:
        return
    page_id = id(page)
    if page_id in dialog_state["attached_pages"]:
        return
    page_on = getattr(page, "on", None)
    if not callable(page_on):
        return

    def _on_dialog(dialog):
        asyncio.ensure_future(_handle_auth_dialog(dialog, dialog_state))

    try:
        page_on("dialog", _on_dialog)
    except Exception:
        return
    dialog_state["attached_pages"].add(page_id)


def _install_auth_dialog_handlers(context, dialog_state):
    for page in getattr(context, "pages", []) or []:
        _install_auth_dialog_page_handler(page, dialog_state)

    if dialog_state.get("context_attached"):
        return

    context_on = getattr(context, "on", None)
    if not callable(context_on):
        return

    def _on_page(page):
        _install_auth_dialog_page_handler(page, dialog_state)

    try:
        context_on("page", _on_page)
    except Exception:
        return
    dialog_state["context_attached"] = True


async def _maybe_emit_auth_dialog_manual_attention(dialog_state, status_callback=None):
    record = (dialog_state or {}).get("manual_attention")
    if not record:
        return False
    key = (record.get("category"), record.get("message"))
    if key not in dialog_state["reported"]:
        dialog_state["reported"].add(key)
        if status_callback is not None:
            result = status_callback(_auth_dialog_status_message(record.get("category")), "warning")
            if inspect.isawaitable(result):
                await result
    return True

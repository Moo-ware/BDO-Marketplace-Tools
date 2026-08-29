import asyncio
import inspect
import re
import weakref


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
AUTH_DIALOG_TASK_DRAIN_TIMEOUT_SECONDS = 1.0


async def _maybe_await(value):
    if inspect.isawaitable(value):
        return await value
    return value


def _new_auth_dialog_state():
    return {
        "attached_pages": weakref.WeakSet(),
        "tasks": set(),
        "manual_attention": None,
        "reported": set(),
    }


def _reset_auth_dialog_state(dialog_state):
    """Reset per-auth-attempt results while preserving installed page listeners."""
    tasks = dialog_state.setdefault("tasks", set())
    for task in tuple(tasks):
        if not task.done():
            task.cancel()
    _prune_auth_dialog_pages(dialog_state)
    dialog_state["manual_attention"] = None
    dialog_state["reported"].clear()
    return dialog_state


def _prune_auth_dialog_pages(dialog_state):
    """Forget closed pages without retaining old Patchright page objects."""
    attached_pages = dialog_state.setdefault("attached_pages", weakref.WeakSet())
    for page in tuple(attached_pages):
        is_closed = getattr(page, "is_closed", None)
        try:
            if callable(is_closed) and is_closed():
                attached_pages.discard(page)
        except Exception:
            pass
    return attached_pages


async def _drain_auth_dialog_tasks(
    dialog_state,
    timeout_seconds=AUTH_DIALOG_TASK_DRAIN_TIMEOUT_SECONDS,
):
    tasks = tuple((dialog_state or {}).get("tasks", ()))
    if not tasks:
        return set()

    # Dialog classification is recorded before the accept/dismiss await. Once an auth cycle ends,
    # cancel any unfinished browser action and give it a short bounded drain; a driver operation
    # that ignores cancellation must not hold cookie capture, parking, or shutdown indefinitely.
    for task in tasks:
        if not task.done():
            task.cancel()
    done, pending = await asyncio.wait(tasks, timeout=max(0.0, float(timeout_seconds)))
    if done:
        await asyncio.gather(*done, return_exceptions=True)
    for task in pending:
        task.cancel()
    return pending


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
    attached_pages = dialog_state["attached_pages"]
    if page in attached_pages:
        return
    page_on = getattr(page, "on", None)
    if not callable(page_on):
        return

    def _on_dialog(dialog):
        task = asyncio.ensure_future(_handle_auth_dialog(dialog, dialog_state))
        dialog_state["tasks"].add(task)
        task.add_done_callback(dialog_state["tasks"].discard)

    try:
        page_on("dialog", _on_dialog)
    except Exception:
        return
    attached_pages.add(page)


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

"""Developer-owned live probes and their task lifecycle."""

import asyncio

from bdo_marketplace_tools.devtools.scenarios import (
    SINGLE_ITEM_TEST_TARGET,
    parse_single_item_stock_response,
)
async def check_single_item_stock(api_handler, target=None):
    """Query one public marketplace row without using an authenticated session."""
    target = target or SINGLE_ITEM_TEST_TARGET
    context = f"{target['name']} public single-item stock check"
    response_json = await api_handler.get_world_market_sublist(
        target["main_key"],
        key_type=target.get("key_type", 0),
        context=context,
    )
    return parse_single_item_stock_response(response_json, target, context)


class SingleItemProbeController:
    """Own the optional single-item polling task outside production orchestration."""

    def __init__(self, manager, api_handler, *, target=None):
        self.manager = manager
        self.api_handler = api_handler
        self.target = dict(target or SINGLE_ITEM_TEST_TARGET)
        self.task = None
        self.purchase_enabled = False
        self.started_at = None
        self.cycle_errors = 0
        self._stop_event = asyncio.Event()

    @property
    def running(self):
        return bool(self.task is not None and not self.task.done())

    async def start(self, *, allow_purchase=False):
        if self.manager.checker_task is not None and not self.manager.checker_task.done():
            return False
        if self.running:
            return False

        loop = asyncio.get_running_loop()
        self.purchase_enabled = bool(allow_purchase)
        self.started_at = loop.time()
        self.cycle_errors = 0
        self._stop_event = asyncio.Event()
        task = asyncio.create_task(self._run())
        self.task = task
        task.add_done_callback(self._handle_done)
        return True

    async def stop(self):
        task = self.task
        was_running = bool(task is not None and not task.done())
        self._stop_event.set()
        if task is not None and task is not asyncio.current_task() and not task.done():
            if not self.manager.purchase_owned_by(task):
                task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        if self.task is task:
            self.task = None
        self.purchase_enabled = False
        self.started_at = None
        return was_running

    async def shutdown(self):
        return await self.stop()

    def _handle_done(self, task):
        if not task.cancelled():
            try:
                error = task.exception()
            except asyncio.CancelledError:
                error = None
            if error is not None:
                self.manager.add_event(
                    f"Single-item test monitor stopped after an unexpected error: {error}",
                    "error",
                )

        if self.task is task:
            self.task = None
            self.purchase_enabled = False
            self.started_at = None

    async def _run(self):
        item_name = self.target["name"]
        while not self._stop_event.is_set():
            try:
                buy_list = await check_single_item_stock(self.api_handler, self.target)
                await self.manager.process_detected_outfits(
                    buy_list,
                    allow_purchase=self.purchase_enabled,
                    item_noun="test item",
                    adjust_pricing=False,
                    purchase_stop_event=self._stop_event,
                )
                self.cycle_errors = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.cycle_errors += 1
                self.manager.add_event(f"{item_name} test scan failed: {exc}", "error")

            if self._stop_event.is_set():
                break

            sleep_duration = self.manager.next_sleep_duration_for_errors(self.cycle_errors)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=sleep_duration)
            except asyncio.TimeoutError:
                pass

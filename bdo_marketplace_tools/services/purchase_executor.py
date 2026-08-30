"""Purchase execution boundary for production and developer runtimes."""


class ApiPurchaseExecutor:
    """Submit purchases through the configured marketplace API handler."""

    def __init__(self, api_handler):
        self.api_handler = api_handler

    async def submit(
        self,
        buy_list,
        *,
        auth_context,
        purchase_delay_bounds,
        on_purchase,
        stop_event=None,
    ):
        del auth_context  # The API executor has no synthetic context-dependent behavior.
        kwargs = {
            "purchase_delay_bounds": purchase_delay_bounds,
            "on_purchase": on_purchase,
        }
        if stop_event is not None:
            kwargs["stop_event"] = stop_event
        return await self.api_handler.buy_item(buy_list, **kwargs)

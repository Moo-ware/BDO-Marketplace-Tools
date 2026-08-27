"""Price conversion rules for outfit-category detections.

The fast public category scan returns the displayed category/base price for an
item row. For pearl outfits this is not the price we submit to `BuyItem`; it is
the median/base price shown in the broad marketplace list. Before buying,
convert it into the maximum valid marketplace price.
"""

PREMIUM_OUTFIT_FAKE_BASE_PRICE = "2020000000"
PREMIUM_OUTFIT_MAX_PRICE = "2170000000"

CLASSIC_OUTFIT_FAKE_BASE_PRICE = "1630000000"
CLASSIC_OUTFIT_MAX_PRICE = "1750000000"

OUTFIT_SET_FAKE_BASE_PRICE = "1100000000"
OUTFIT_SET_MAX_PRICE = "1180000000"

DIRECT_PRICE_PASSTHROUGH = "25200"

PRICE_MULTIPLIER_NUMERATOR = 43
PRICE_MULTIPLIER_DENOMINATOR = 40
PRICE_TICK_1M = 1_000_000
PRICE_TICK_5M = 5_000_000
PRICE_TICK_10M = 10_000_000

# Explicit non-outfit compatibility overrides. Production outfit rows use the
# formula below, including the three historical box prices named above.
PRICE_OVERRIDES = {
    DIRECT_PRICE_PASSTHROUGH: DIRECT_PRICE_PASSTHROUGH,
}

def maximum_market_price_from_median(median_price):
    """Return the max marketplace price using exact integer arithmetic.

    The observed rule is median + 7.5%, rounded down to the valid tick selected
    from the raw (pre-rounded) maximum: 1M below 500M, 5M below 1B, and 10M at
    or above 1B. Using 43/40 avoids float rounding at bracket boundaries.
    """
    try:
        median = int(str(median_price))
    except (TypeError, ValueError):
        raise ValueError(f"Invalid outfit median price: {median_price!r}") from None
    if median <= 0:
        raise ValueError("Outfit median price must be positive.")

    raw_numerator = median * PRICE_MULTIPLIER_NUMERATOR
    if raw_numerator >= 1_000_000_000 * PRICE_MULTIPLIER_DENOMINATOR:
        tick_size = PRICE_TICK_10M
    elif raw_numerator >= 500_000_000 * PRICE_MULTIPLIER_DENOMINATOR:
        tick_size = PRICE_TICK_5M
    else:
        tick_size = PRICE_TICK_1M

    max_price = (
        raw_numerator // (PRICE_MULTIPLIER_DENOMINATOR * tick_size)
    ) * tick_size
    if max_price <= 0:
        raise ValueError("Outfit median price is below the supported marketplace tick range.")
    return max_price


def apply_price_rules(buy_list):
    """Convert detected median/base prices to max buy prices.

    Input rows come from the broad category scanner as:
    `[item_id, stock_count, detected_fake_base_price]`.

    Output rows keep the same shape, but the third value is the max buy price to
    submit to `BuyItem`.
    """
    adjusted = []

    for item_id, stock, detected_fake_base_price in buy_list:
        normalized_fake_base_price = str(detected_fake_base_price)
        max_buy_price = PRICE_OVERRIDES.get(normalized_fake_base_price)
        if max_buy_price is None:
            max_buy_price = str(maximum_market_price_from_median(normalized_fake_base_price))

        adjusted.append([str(item_id), str(stock), max_buy_price])

    return adjusted


def purchase_record_spend(purchase_records):
    total = 0
    for record in purchase_records:
        total += int(record["price"]) * int(record.get("count", 1))
    return total


def purchase_record_count(purchase_records):
    return sum(int(record.get("count", 1)) for record in purchase_records)

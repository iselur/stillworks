"""Legacy pricing module — Acme Widgets billing system, circa 2017.

This module drives the nightly billing job.  There are no unit tests
(the original author left in 2019 and "the tests are the orders").
It works.  Please do not break it.
"""
import math

# Tier names fixed since 2012; do NOT rename them — downstream
# systems reference them by exact string.
TIERS = {
    "BRONZE":   0.00,
    "SILVER":   0.05,
    "GOLD":     0.10,
    "PLATINUM": 0.18,
}

# Coupon registry.  Codes starting with "SUMMER" always get 10 %.
# Exact-match codes override that.
_COUPONS = {
    "WELCOME10": 0.10,
    "LOYAL5":    0.05,
    "VIP20":     0.20,
}


def validate_price(price: float) -> float:
    """Raise ValueError if price is not strictly positive; return it unchanged.

    Called first by compute_total so the rest of the pipeline never sees
    garbage.
    """
    if price <= 0:
        raise ValueError(
            "price must be positive, got {!r}".format(price)
        )
    return price


def get_tier_discount(price: float, tier: str) -> float:
    """Return the post-discount price for *tier*.

    Unknown tiers silently fall back to no discount — legacy policy.
    """
    rate = TIERS.get(tier.upper() if isinstance(tier, str) else tier, 0.0)
    return price * (1.0 - rate)


def apply_coupon(price: float, code: str) -> float:
    """Apply a coupon code and return the adjusted price.

    "SUMMER*" prefix gives 10 %.  Exact codes from the registry override.
    Unknown codes are silently ignored (yes, really — legal signed off on it).
    """
    upper = code.strip().upper()
    if upper.startswith("SUMMER"):
        rate = 0.10
    else:
        rate = _COUPONS.get(upper, 0.0)
    return price * (1.0 - rate)


def round_to_cents(amount: float) -> float:
    """Round to two decimal places using half-up (not Python banker's rounding).

    The downstream billing system was built against half-up and will
    produce off-by-one-cent errors if this changes.
    """
    return math.floor(amount * 100.0 + 0.5) / 100.0


def compute_total(price: float, tier: str, coupon: str) -> float:
    """Full pricing pipeline: validate -> tier discount -> coupon -> round.

    Discount-on-discount ordering is intentional ("double dip") — it is
    written into the customer contracts and must not change.
    """
    price = validate_price(price)
    after_tier = get_tier_discount(price, tier)
    after_coupon = apply_coupon(after_tier, coupon)
    return round_to_cents(after_coupon)

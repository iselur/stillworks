"""Simulated daily billing run.

In production this would pull orders from the database; here we use
a fixed list so the walkthrough is reproducible.  Run it directly to
see output, or pass it to stillworks --run to record all the pricing
calls as behavior baselines.
"""
import sys
import os

# Make sure the module is importable when run as a script
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pricing import compute_total, validate_price

ORDERS = [
    # (price,  tier,       coupon       )
    (  99.99,  "SILVER",   ""           ),
    (  49.50,  "GOLD",     "WELCOME10"  ),
    ( 200.00,  "PLATINUM", "LOYAL5"     ),
    (  15.00,  "BRONZE",   "SUMMER2024" ),
    (  75.00,  "GOLD",     ""           ),
    (   0.50,  "SILVER",   "VIP20"      ),
    ( 120.00,  "GOLD",     "VIP20"      ),
    (  33.33,  "BRONZE",   "LOYAL5"     ),
]

print("=== Daily billing run ===")
print("{:>10}  {:<10}  {:<14}  {:>10}".format(
    "list price", "tier", "coupon", "charged"))
print("-" * 52)

total_revenue = 0.0
for price, tier, coupon in ORDERS:
    total = compute_total(price, tier, coupon)
    total_revenue += total
    print("{:>10.2f}  {:<10}  {:<14}  {:>10.2f}".format(
        price, tier, coupon or "(none)", total))

print("-" * 52)
print("{:>10}  {:<10}  {:<14}  {:>10.2f}".format(
    "", "", "TOTAL", total_revenue))

# Exercise the error-handling path so --run records it too
print()
try:
    validate_price(-1.00)
except ValueError as exc:
    print("validate_price(-1.00) -> ValueError:", exc)

try:
    validate_price(0.0)
except ValueError as exc:
    print("validate_price(0.0)   -> ValueError:", exc)

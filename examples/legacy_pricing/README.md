# Example: legacy pricing module

`pricing.py` is a realistic crufty-but-working module with no existing
tests.  This walkthrough shows the full stillworks workflow against it:
lock, let an AI refactor, catch the injected regression, fix it, accept
one intentional change, and generate an evidence report.

All commands below are copy-pasteable from the `examples/legacy_pricing/`
directory.

---

## The files

| file | what it is |
|---|---|
| `pricing.py` | ~70-line legacy billing module (tiers, coupons, rounding, error on bad input) |
| `daily_run.py` | script that calls every pricing function the way production does |

---

## Step 1 — lock current behavior

### Fuzz mode (type-annotation-driven)

stillworks reads the function signatures and generates inputs automatically.

```
PYTHONPATH=/path/to/stillworks python3 -m stillworks lock pricing.py --fuzz 8
```

Real output:

```
locked 38 records (38 calls, 0 commands) -> .stillworks/lock.json
```

38 probes across `validate_price`, `get_tier_discount`, `apply_coupon`,
`round_to_cents`, and `compute_total` — including the `ValueError` raised
by `validate_price(-1)`, which is itself recorded as expected behavior.

### Run mode (real-usage recording)

Passing `--run daily_run.py` wraps every call the script makes into
`pricing.py` and records those inputs too.  The two flags combine: fuzz
fills coverage gaps, run captures the exact production call patterns.

```
PYTHONPATH=/path/to/stillworks python3 -m stillworks lock pricing.py --fuzz 8 --run daily_run.py
```

Real output (script output printed first, then the lock summary):

```
=== Daily billing run ===
list price  tier        coupon             charged
----------------------------------------------------
     99.99  SILVER      (none)               94.99
     49.50  GOLD        WELCOME10            40.10
    200.00  PLATINUM    LOYAL5              155.80
     15.00  BRONZE      SUMMER2024           13.50
     75.00  GOLD        (none)               67.50
      0.50  SILVER      VIP20                 0.38
    120.00  GOLD        VIP20                86.40
     33.33  BRONZE      LOYAL5               31.66
----------------------------------------------------
                        TOTAL               490.33

validate_price(-1.00) -> ValueError: price must be positive, got -1.0
validate_price(0.0)   -> ValueError: price must be positive, got 0.0
locked 48 records (48 calls, 0 commands) -> .stillworks/lock.json
```

### Confirm baseline is green

```
PYTHONPATH=/path/to/stillworks python3 -m stillworks check
```

Real output:

```
STILL WORKS: 48 records — 48 OK
```

---

## Step 2 — ask your AI to refactor

Now hand `pricing.py` to your coding agent.  A typical prompt:

> Refactor pricing.py.  Extract the coupon lookup into a helper, rename
> the internal `_COUPONS` dict to something clearer, and clean up the
> comments.  Do not change any numeric behavior.

The agent edits the file.  Unknown to it, the refactor accidentally
changed the `GOLD` tier discount from `0.10` to `0.12` (an off-by-one
in the rates table).

---

## Step 3 — check catches the regression

```
PYTHONPATH=/path/to/stillworks python3 -m stillworks check
```

Real output (exit code 1):

```
CHANGED  compute_total#2  (compute_total)
         args: ((49.5, 'GOLD', 'WELCOME10'), {})
         was:  40.1
         now:  39.2
CHANGED  compute_total#5  (compute_total)
         args: ((75.0, 'GOLD', ''), {})
         was:  67.5
         now:  66.0
CHANGED  compute_total#7  (compute_total)
         args: ((120.0, 'GOLD', 'VIP20'), {})
         was:  86.4
         now:  84.48
BEHAVIOR CHANGED: 48 records — 3 CHANGED, 45 OK
```

Three records changed — all involving GOLD orders.  The agent revert the
accidental rate change.

---

## Step 4 — fix the bug; one change remains intentional

After reverting the GOLD regression, marketing emails say the `WELCOME10`
coupon rate should be bumped from 10 % to 12 % starting today.  The
agent makes that update intentionally.

Run check again:

```
PYTHONPATH=/path/to/stillworks python3 -m stillworks check
```

Real output (exit code 1):

```
CHANGED  compute_total#2  (compute_total)
         args: ((49.5, 'GOLD', 'WELCOME10'), {})
         was:  40.1
         now:  39.2
BEHAVIOR CHANGED: 48 records — 1 CHANGED, 47 OK
```

One record changed — the order that used `WELCOME10`.  This is the
intended update.

---

## Step 5 — accept the intended change

```
PYTHONPATH=/path/to/stillworks python3 -m stillworks accept compute_total#2
```

Real output:

```
accepted new behavior: compute_total#2
```

Rerun check:

```
PYTHONPATH=/path/to/stillworks python3 -m stillworks check
```

Real output:

```
STILL WORKS: 48 records — 48 OK
```

---

## Step 6 — generate the evidence report

Attach this to the PR so reviewers know what was verified.

```
PYTHONPATH=/path/to/stillworks python3 -m stillworks report
```

Real output (trimmed):

```
# stillworks evidence report

**Target:** `pricing.py`
**Baseline locked:** 2026-07-29T22:21:25
**Records:** 48 total — 48 function calls, 0 commands

## Last verification (2026-07-29T22:22:23)

**Verdict:** PASS — behavior unchanged

| status | count | meaning |
|---|---|---|
| OK | 48 | reproduced exactly |

## Accepted changes

- 2026-07-29T22:22:18 — `compute_total#2`: accepted change
  - was: `40.1`
  - now: `39.2`

## Environment

- Python 3.12.3
- Linux 6.8.0-111-generic
- stillworks lockfile schema v1
```

---

## What the lockfile covers

| function | what was exercised |
|---|---|
| `validate_price` | positive values, zero, negative (raises ValueError) |
| `get_tier_discount` | all four tier strings, unknown tier, every constant from the rates table |
| `apply_coupon` | exact-match codes, SUMMER prefix, empty string, unknown code |
| `round_to_cents` | values near 0.5-cent boundaries (half-up vs banker's rounding) |
| `compute_total` | cross-product of real orders from `daily_run.py` + fuzz inputs |

Delete `.stillworks/` when the renovation is done.  No test suite to
maintain — the lockfile was scaffolding, not production code.

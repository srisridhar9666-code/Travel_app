"""
Money: how it is split, and why it is never a float (SOW section 2 and 6,
addendum B5 / C1).

The SOW asks for Campaign Financials and Cost Analytics without saying who
enters a number or when, which is what made both sections unbuildable. C1's
recommendation is implemented here: an admin confirms the cost at booking,
pre-filled from the ticket, in INR, and a shared cost splits evenly with a
manual override.

Two rules that look small and are not:

* **Everything is `Decimal`.** A shared cab at ₹1,000.10 across three people is
  not representable in binary floating point, and a reporting system whose totals
  drift by a paisa per row is a reporting system nobody trusts by the end of the
  quarter.
* **A split sums to exactly what was entered.** Dividing 1000 by 3 gives
  333.33, 333.33 and **333.34** - the remainder goes somewhere explicit rather
  than being rounded away. `split_evenly` is the only function allowed to
  apportion money, so that rule holds everywhere.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

#: Two decimal places, because the currency is INR and paise are real.
PAISA = Decimal("0.01")

#: Single-currency by design. C1's answer is INR; storing the code keeps the
#: column honest and leaves room for a second currency later, but nothing here
#: converts between them and nothing pretends to.
DEFAULT_CURRENCY = "INR"


def to_money(value) -> Decimal:
    """Coerce to a 2dp Decimal, going through `str` so a float never poisons it.

    `Decimal(0.1)` is 0.1000000000000000055511151231257827, and quantising that
    is how a rounding bug gets into a financial report. `Decimal("0.1")` is not.
    """
    if value is None:
        raise ValueError("amount is required")
    if isinstance(value, Decimal):
        amount = value
    else:
        amount = Decimal(str(value))
    return amount.quantize(PAISA, rounding=ROUND_HALF_UP)


def split_evenly(total, shares: int) -> list[Decimal]:
    """Divide `total` into `shares` parts that sum to exactly `total`.

    The base amount is rounded down and the leftover paise are handed out one at
    a time from the first share. Three people sharing ₹1,000 get 333.34, 333.33,
    333.33 - never 333.33 three times with a rupee quietly lost, and never
    333.34 three times with two paise invented.

    The first share carries the extra rather than the last, so the requester -
    who is always first on their own request - absorbs the odd paisa rather than
    a colleague being charged more than the person who booked it.
    """
    if shares < 1:
        raise ValueError("a cost must be split across at least one person")

    amount = to_money(total)
    if amount < 0:
        raise ValueError("a cost cannot be negative")

    base = (amount / shares).quantize(PAISA, rounding="ROUND_DOWN")
    remainder = amount - (base * shares)

    # `remainder` is always a whole number of paise, and always less than
    # `shares` of them, so this loop hands out at most one extra paisa each.
    extra_paise = int((remainder / PAISA).to_integral_value())

    return [base + (PAISA if i < extra_paise else Decimal("0.00")) for i in range(shares)]


# Currency *formatting* deliberately lives on the frontend, not here.
# `Intl.NumberFormat('en-IN')` already does Indian digit grouping - 12,34,567.89
# rather than 1,234,567.89 - and keeping the rupee symbol out of Python avoids a
# UnicodeEncodeError the first time an amount reaches a Windows console log.

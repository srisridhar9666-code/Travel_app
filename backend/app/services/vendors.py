"""
Recording who was paid for a trip.

An admin names the vendor when they enter a cost or record the cab that was
sent. The vendor is what an invoice is reconciled against, so the rules for
naming one live here, once, for every path that sets it.
"""
from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.request import RequestTraveller
from app.models.vendor import Vendor


def pick(
    db: Session, tenant_id: str, vendor_id: int | None, *, keeping: set[int] | None = None
) -> Vendor | None:
    """The vendor an admin chose, checked: one of this organisation's, and active.

    `keeping` is the vendors the travellers already have. Saving a cost again
    with the vendor it already had is not choosing a switched-off vendor anew,
    so it is allowed - otherwise switching a vendor off would stop anyone
    correcting a typo in a cost paid to them.
    """
    if vendor_id is None:
        return None
    vendor = db.get(Vendor, vendor_id)
    if vendor is None or vendor.tenant_id != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="That vendor is not on your list. Pick one from Vendors.",
        )
    if not vendor.is_active and vendor.id not in (keeping or set()):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{vendor.name} is switched off. Pick an active vendor, or switch it back on.",
        )
    return vendor


def name_of(traveller: RequestTraveller) -> str | None:
    return traveller.vendor.name if traveller.vendor is not None else None

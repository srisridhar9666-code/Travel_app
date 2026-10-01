"""
Cost entry and reporting (SOW sections 2 and 6, addendum B5 / C1).

Both SOW sections were unbuildable as written because nothing said where a
number comes from. C1's recommendation is what is built here:

* an **admin** enters the cost, at booking, pre-filled from the ticket;
* the currency is **INR**;
* a shared cab or room **splits evenly**, with a manual override.

Cost is admin-only to read as well as to write. A ground-staff member seeing
what a colleague's flight cost is a personnel problem nobody asked for, and
nothing in section 6 needs it.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.core.deps import AdminUser, DbSession
from app.core.enums import AuditAction, TravellerStatus
from app.models.base import naive_utcnow
from app.models.request import RequestTraveller, TravelRequest
from app.schemas.analytics import (
    AnalyticsBundle,
    CampaignSpend,
    CostEntry,
    CostPreview,
    CostPreviewRow,
    CostSplit,
    DeploymentRow,
    MonthSpend,
    Overview,
    TypeSpend,
    UncostedRow,
)
from app.schemas.request import RequestRead
from app.services import analytics, audit, costs
from app.services import requests as svc

router = APIRouter(tags=["analytics"])


def _load(db: Session, request_id: int, tenant_id: str) -> TravelRequest:
    row = db.get(TravelRequest, request_id)
    if row is None or row.tenant_id != tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found.")
    return row


def _travellers(row: TravelRequest, ids: list[int]) -> list[RequestTraveller]:
    by_id = {t.id: t for t in row.travellers}
    missing = [i for i in ids if i not in by_id]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Traveller(s) {', '.join(str(m) for m in missing)} are not on this request.",
        )
    return [by_id[i] for i in ids]


def _assert_costable(traveller: RequestTraveller) -> None:
    """A cost belongs to a trip that is happening.

    Recording spend against a rejected traveller would quietly inflate a
    campaign's total with money nobody paid.
    """
    if traveller.status in (TravellerStatus.REJECTED, TravellerStatus.CANCELLED):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"{traveller.user.full_name} was "
                f"{str(traveller.status).lower()}, so there is no cost to record."
            ),
        )


def _apply(
    db: Session,
    *,
    traveller: RequestTraveller,
    amount,
    note: str | None,
    actor,
) -> dict:
    before = traveller.cost_amount
    traveller.cost_amount = costs.to_money(amount) if amount is not None else None
    traveller.cost_currency = costs.DEFAULT_CURRENCY
    traveller.cost_note = (note or "").strip() or None
    traveller.cost_entered_by_id = actor.id
    traveller.cost_entered_at = naive_utcnow()
    return {
        "traveller": traveller.user.full_name,
        "from": str(before) if before is not None else None,
        "to": str(traveller.cost_amount) if traveller.cost_amount is not None else None,
    }


# ---------------------------------------------------------------------------
# Cost entry
# ---------------------------------------------------------------------------


@router.post("/requests/{request_id}/costs", response_model=RequestRead)
def set_costs(
    request_id: int,
    payload: CostEntry,
    actor: AdminUser,
    http_request: Request,
    db: DbSession,
) -> RequestRead:
    """Record what each person's travel cost - the explicit, override path.

    Also the ordinary path for a single traveller, where "splitting" a cost one
    way would be a strange way to describe typing a number in.
    """
    row = _load(db, request_id, actor.tenant_id)

    ids = [a.traveller_id for a in payload.amounts]
    if len(ids) != len(set(ids)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The same traveller appears twice in this payload.",
        )
    travellers = _travellers(row, ids)

    changes = []
    for entry, traveller in zip(payload.amounts, travellers, strict=True):
        _assert_costable(traveller)
        changes.append(
            _apply(db, traveller=traveller, amount=entry.amount, note=entry.note, actor=actor)
        )

    audit.record(
        db,
        action=AuditAction.UPDATE,
        entity_type="travel_request",
        entity_id=row.id,
        summary=f"{actor.full_name} recorded cost on request #{row.id}",
        changes={"costs": changes},
        tenant_id=actor.tenant_id,
        actor=actor,
        request=http_request,
    )
    db.commit()
    db.refresh(row)
    return svc.to_read(db, row, tenant_id=actor.tenant_id, viewer=actor)


@router.post("/requests/{request_id}/costs/preview", response_model=CostPreview)
def preview_split(
    request_id: int, payload: CostSplit, actor: AdminUser, db: DbSession
) -> CostPreview:
    """What an even split would come to, without saving it.

    Exists so the admin sees the actual paise before committing - including the
    odd one on the first row, which otherwise looks like a bug the first time
    someone divides a thousand rupees by three.
    """
    row = _load(db, request_id, actor.tenant_id)
    travellers = _travellers(row, payload.traveller_ids)
    shares = costs.split_evenly(payload.total_amount, len(travellers))

    return CostPreview(
        total_amount=costs.to_money(payload.total_amount),
        rows=[
            CostPreviewRow(
                traveller_id=t.id, traveller_name=t.user.full_name, amount=share
            )
            for t, share in zip(travellers, shares, strict=True)
        ],
        sums_to_total=sum(shares) == costs.to_money(payload.total_amount),
    )


@router.post("/requests/{request_id}/costs/split", response_model=RequestRead)
def split_cost(
    request_id: int,
    payload: CostSplit,
    actor: AdminUser,
    http_request: Request,
    db: DbSession,
) -> RequestRead:
    """Share one total evenly across several people and save it.

    The shared cab and the shared room, which is the case C1 specifically asks
    about. The apportionment sums to exactly the total; the first traveller
    absorbs any odd paisa.
    """
    row = _load(db, request_id, actor.tenant_id)

    if len(payload.traveller_ids) != len(set(payload.traveller_ids)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The same traveller appears twice, so the split would be wrong.",
        )
    travellers = _travellers(row, payload.traveller_ids)
    for traveller in travellers:
        _assert_costable(traveller)

    shares = costs.split_evenly(payload.total_amount, len(travellers))
    note = payload.note or f"Shared cost, split {len(travellers)} ways"

    changes = [
        _apply(db, traveller=traveller, amount=share, note=note, actor=actor)
        for traveller, share in zip(travellers, shares, strict=True)
    ]

    audit.record(
        db,
        action=AuditAction.UPDATE,
        entity_type="travel_request",
        entity_id=row.id,
        summary=(
            f"{actor.full_name} split {costs.to_money(payload.total_amount)} "
            f"across {len(travellers)} traveller(s) on request #{row.id}"
        ),
        changes={"total": str(costs.to_money(payload.total_amount)), "costs": changes},
        tenant_id=actor.tenant_id,
        actor=actor,
        request=http_request,
    )
    db.commit()
    db.refresh(row)
    return svc.to_read(db, row, tenant_id=actor.tenant_id, viewer=actor)


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


@router.get("/analytics", response_model=AnalyticsBundle)
def bundle(
    actor: AdminUser,
    db: DbSession,
    days: Annotated[int, Query(ge=1, le=730)] = 90,
    months: Annotated[int, Query(ge=1, le=24)] = 6,
) -> AnalyticsBundle:
    """Everything the dashboard shows, in one round trip.

    One call rather than six, because the figures have to agree with each other
    on screen and six independent requests can land either side of a booking
    being confirmed.
    """
    return AnalyticsBundle(
        overview=Overview(**analytics.overview(db, actor.tenant_id, days=days)),
        by_campaign=[CampaignSpend(**r) for r in analytics.by_campaign(db, actor.tenant_id)],
        by_type=[TypeSpend(**r) for r in analytics.by_type(db, actor.tenant_id)],
        by_month=[MonthSpend(**r) for r in analytics.by_month(db, actor.tenant_id, months=months)],
        deployment=[DeploymentRow(**r) for r in analytics.deployment(db, actor.tenant_id)],
        uncosted=[UncostedRow(**r) for r in analytics.uncosted_bookings(db, actor.tenant_id)],
    )


@router.get("/analytics/campaigns", response_model=list[CampaignSpend])
def campaign_spend(actor: AdminUser, db: DbSession) -> list[CampaignSpend]:
    """Campaign Financials (SOW section 2) on their own, for the projects screen."""
    return [CampaignSpend(**r) for r in analytics.by_campaign(db, actor.tenant_id)]


@router.get("/analytics/uncosted", response_model=list[UncostedRow])
def uncosted(actor: AdminUser, db: DbSession) -> list[UncostedRow]:
    """Booked travellers with no cost recorded - the admin's worklist."""
    return [UncostedRow(**r) for r in analytics.uncosted_bookings(db, actor.tenant_id)]

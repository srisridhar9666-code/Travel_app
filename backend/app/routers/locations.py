"""
The list of places a request can name.

Readable by anyone signed in, because the request form needs it. Only an admin
may add to it - an open endpoint would let the typos back in through a different
door, which is the whole thing this is meant to prevent.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from app.core.deps import AdminUser, CurrentUser, DbSession
from app.core.enums import AuditAction
from app.models.location import Location
from app.services import audit
from app.services import locations as service

router = APIRouter(prefix="/locations", tags=["locations"])


class LocationCreate(BaseModel):
    state: str = Field(min_length=2, max_length=80)
    city: str = Field(min_length=2, max_length=120)

    @field_validator("state", "city")
    @classmethod
    def _tidy(cls, value: str) -> str:
        # Title case with the inner spacing collapsed, so "  new   DELHI " and
        # "New Delhi" cannot both end up in the list.
        return " ".join(value.split()).title()


class LocationRead(BaseModel):
    id: int
    state: str
    city: str


@router.get("", response_model=dict[str, list[str]])
def list_locations(
    user: CurrentUser,
    db: DbSession,
    include_inactive: Annotated[bool, Query()] = False,
) -> dict[str, list[str]]:
    """Cities grouped by state, which is the shape the cascading picker wants.

    Returned as a plain mapping rather than a list of rows: the form needs
    "given this state, which cities", and making the client regroup a flat list
    on every render is work for nothing.
    """
    service.ensure_seeded(db, user.tenant_id)

    stmt = select(Location).where(Location.tenant_id == user.tenant_id)
    if not include_inactive:
        stmt = stmt.where(Location.is_active.is_(True))

    grouped: dict[str, list[str]] = {}
    for row in db.execute(stmt.order_by(Location.state, Location.city)).scalars():
        grouped.setdefault(row.state, []).append(row.city)
    return grouped


@router.post("", response_model=LocationRead, status_code=status.HTTP_201_CREATED)
def add_location(
    payload: LocationCreate, actor: AdminUser, request: Request, db: DbSession
) -> LocationRead:
    """Add a city the seed list did not have."""
    clash = db.execute(
        select(Location).where(
            Location.tenant_id == actor.tenant_id,
            Location.state == payload.state,
            Location.city == payload.city,
        )
    ).scalar_one_or_none()

    if clash is not None:
        if not clash.is_active:
            clash.is_active = True
            db.commit()
            return LocationRead(id=clash.id, state=clash.state, city=clash.city)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{payload.city}, {payload.state} is already on the list.",
        )

    row = Location(tenant_id=actor.tenant_id, state=payload.state, city=payload.city)
    db.add(row)
    db.flush()

    audit.record(
        db,
        action=AuditAction.CREATE,
        entity_type="location",
        entity_id=row.id,
        summary=f"{actor.full_name} added {row.city}, {row.state} to the place list",
        tenant_id=actor.tenant_id,
        actor=actor,
        request=request,
    )
    db.commit()
    return LocationRead(id=row.id, state=row.state, city=row.city)


@router.get("/resolve")
def resolve(
    typed: Annotated[str, Query(max_length=120)], user: CurrentUser, db: DbSession
) -> dict:
    """What a free-text place name probably meant.

    For reading rows written before the picker existed - "hyd" and "Hyderabad"
    are the same place and a report that treats them as two is wrong.
    """
    found = service.match(db, user.tenant_id, typed)
    return {
        "typed": typed,
        "matched": bool(found),
        "city": found.city if found else None,
        "state": found.state if found else None,
    }

"""
The seed list of Indian states and places, and the matching that keeps old and
typed values usable.

The list lives in `app/data/india_places.json`: every state and union territory,
with its major cities, its districts and its assembly constituencies. Field
campaigns are planned constituency by constituency, so a list of only the big
cities left most real destinations off the picker. Anything still missing is
added by an admin through the API, or typed under "Other" on the form.
"""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.location import Location

logger = logging.getLogger(__name__)

PLACES_FILE = Path(__file__).resolve().parent.parent / "data" / "india_places.json"

#: The order places are taken from each state's entry. Earlier buckets win when
#: two spell the same place, so "Hyderabad" the city is kept over "Hyderabad"
#: the district.
_BUCKETS = ("cities", "districts", "constituencies")


@lru_cache
def load_places() -> dict[str, list[str]]:
    """Every seed place, grouped by state, de-duplicated and sorted."""
    raw = json.loads(PLACES_FILE.read_text(encoding="utf-8"))["states"]
    grouped: dict[str, list[str]] = {}
    for state, buckets in raw.items():
        seen: dict[str, str] = {}
        for bucket in _BUCKETS:
            for place in buckets.get(bucket, []):
                place = " ".join(place.split())
                if place and normalise(place) not in seen:
                    seen[normalise(place)] = place
        grouped[state] = sorted(seen.values(), key=str.lower)
    return grouped


#: Abbreviations and spellings that people actually type, mapped to the
#: canonical city. Used to match historical free-text values and places typed
#: under "Other", so a typed abbreviation still lands on the listed city.
ALIASES: dict[str, str] = {
    "hyd": "Hyderabad",
    "blr": "Bengaluru",
    "bangalore": "Bengaluru",
    "bengaluru": "Bengaluru",
    "bom": "Mumbai",
    "bombay": "Mumbai",
    "del": "New Delhi",
    "ncr": "New Delhi",
    "maa": "Chennai",
    "madras": "Chennai",
    "ccu": "Kolkata",
    "calcutta": "Kolkata",
    "pnq": "Pune",
    "amd": "Ahmedabad",
    "cok": "Kochi",
    "cochin": "Kochi",
    "trv": "Thiruvananthapuram",
    "trivandrum": "Thiruvananthapuram",
    "vizag": "Visakhapatnam",
    "mysore": "Mysuru",
    "gurgaon": "Gurugram",
    "allahabad": "Prayagraj",
    "pondicherry": "Puducherry",
}


def normalise(value: str) -> str:
    """Collapse a typed place name to a comparison key.

    Accents are folded as well as case and punctuation, matching the column's
    accent-insensitive collation - otherwise "Mahé" and "Mahe" look different
    here and identical to the unique index, and the seed fails on the clash.
    """
    folded = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", folded.lower())


def seed(db: Session, tenant_id: str) -> int:
    """Insert any seed place this tenant does not have. Idempotent.

    Compared on the normalised name, so a place an admin already added as
    "Kukatpally" is not added a second time as "KUKATPALLY", and one an admin
    deactivated stays deactivated.
    """
    existing = {
        (state, normalise(city))
        for state, city in db.execute(
            select(Location.state, Location.city).where(Location.tenant_id == tenant_id)
        ).all()
    }

    added = 0
    for state, places in load_places().items():
        for city in places:
            if (state, normalise(city)) in existing:
                continue
            db.add(Location(tenant_id=tenant_id, state=state, city=city))
            existing.add((state, normalise(city)))
            added += 1

    if added:
        db.flush()
        logger.info("Seeded %d location(s) for %s", added, tenant_id)
    return added


def ensure_seeded(db: Session, tenant_id: str) -> None:
    """Seed this tenant's places if it has none at all.

    Startup normally does this, but startup seeding fails quietly when the API
    comes up before `alembic upgrade head` has created the table, and nothing
    retries it - the form then shows an empty state list until someone restarts
    the server. Checking here, on the read that needs the list, means the first
    request after the migration repairs it.
    """
    count = db.execute(
        select(func.count(Location.id)).where(Location.tenant_id == tenant_id)
    ).scalar_one()
    if count == 0:
        seed(db, tenant_id)
        db.commit()


def tidy(value: str | None) -> str | None:
    """A typed place with its spacing collapsed, or None when it is blank."""
    cleaned = " ".join((value or "").split())
    return cleaned or None


def canonical(db: Session, tenant_id: str, state: str | None, typed: str | None) -> tuple[str | None, str | None]:
    """The (state, place) a request should store for what someone picked or typed.

    A place picked from the list comes back unchanged. One typed under "Other"
    is matched against the list first - "hyd" is Hyderabad, and storing it as
    typed would hide a colleague in the same hotel from co-stay matching. Only
    a place the list genuinely does not have is kept as typed, tidied.
    """
    place = tidy(typed)
    state = tidy(state)
    if place is None:
        return state, None

    rows = db.execute(
        select(Location.state, Location.city).where(Location.tenant_id == tenant_id)
    ).all()
    key = normalise(place)
    aliased = ALIASES.get(key)
    known_states = {normalise(row_state): row_state for row_state, _ in rows}
    if state:
        # "telangana" is Telangana: the state filter and reports group by name.
        state = known_states.get(normalise(state), state)

    if state in known_states.values():
        # The state was picked from the list, so it is not a guess to second-
        # guess: "Aurangabad" stays in whichever of Bihar or Maharashtra was
        # chosen, and "Shamshabad" typed under Telangana stays in Telangana
        # rather than moving to the Shamshabad in Madhya Pradesh.
        for row_state, city in rows:
            if row_state == state and (normalise(city) == key or city == aliased):
                return row_state, city
        return state, place

    # No state, or one not on the list (rows from before the picker): match
    # anywhere, but only when exactly one state has that name - a guess
    # between two would be worse than keeping what they typed.
    anywhere = [(row_state, city) for row_state, city in rows if normalise(city) == key]
    if len(anywhere) == 1:
        return anywhere[0]
    if aliased:
        for row_state, city in rows:
            if city == aliased:
                return row_state, city

    return state, place


def match(db: Session, tenant_id: str, typed: str) -> Location | None:
    """Best-effort resolution of a free-text place to a known city.

    For reading historical rows written before the picker existed. Tries the
    normalised name, then the alias table, then a unique prefix - and gives up
    rather than guessing when more than one city could be meant.
    """
    key = normalise(typed)
    if not key:
        return None

    rows = (
        db.execute(select(Location).where(Location.tenant_id == tenant_id)).scalars().all()
    )
    by_key = {normalise(row.city): row for row in rows}

    if key in by_key:
        return by_key[key]

    aliased = ALIASES.get(key)
    if aliased and normalise(aliased) in by_key:
        return by_key[normalise(aliased)]

    starts = [row for norm, row in by_key.items() if norm.startswith(key)]
    return starts[0] if len(starts) == 1 else None

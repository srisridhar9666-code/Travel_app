"""
The seed list of Indian states and cities, and the matching that keeps old
free-text values usable.

The list is a starting point, not an authority. It covers the places field teams
actually go; anything missing is added by an admin through the API rather than
by editing this file.
"""
from __future__ import annotations

import logging
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.location import Location

logger = logging.getLogger(__name__)

#: States and union territories, with the cities field teams are most likely to
#: travel to. Deliberately not exhaustive - admins add what they need.
SEED: dict[str, list[str]] = {
    "Andhra Pradesh": ["Visakhapatnam", "Vijayawada", "Guntur", "Nellore", "Kurnool", "Tirupati", "Rajahmundry", "Kakinada"],
    "Arunachal Pradesh": ["Itanagar", "Naharlagun", "Pasighat"],
    "Assam": ["Guwahati", "Silchar", "Dibrugarh", "Jorhat", "Tezpur", "Nagaon"],
    "Bihar": ["Patna", "Gaya", "Bhagalpur", "Muzaffarpur", "Darbhanga", "Purnia"],
    "Chhattisgarh": ["Raipur", "Bhilai", "Bilaspur", "Korba", "Durg"],
    "Goa": ["Panaji", "Margao", "Vasco da Gama", "Mapusa"],
    "Gujarat": ["Ahmedabad", "Surat", "Vadodara", "Rajkot", "Bhavnagar", "Jamnagar", "Gandhinagar", "Anand"],
    "Haryana": ["Gurugram", "Faridabad", "Panipat", "Ambala", "Hisar", "Karnal", "Rohtak"],
    "Himachal Pradesh": ["Shimla", "Dharamshala", "Mandi", "Solan", "Kullu"],
    "Jharkhand": ["Ranchi", "Jamshedpur", "Dhanbad", "Bokaro", "Hazaribagh"],
    "Karnataka": ["Bengaluru", "Mysuru", "Hubballi", "Mangaluru", "Belagavi", "Davanagere", "Ballari", "Shivamogga"],
    "Kerala": ["Thiruvananthapuram", "Kochi", "Kozhikode", "Thrissur", "Kollam", "Kannur", "Alappuzha"],
    "Madhya Pradesh": ["Bhopal", "Indore", "Jabalpur", "Gwalior", "Ujjain", "Sagar", "Satna"],
    "Maharashtra": ["Mumbai", "Pune", "Nagpur", "Nashik", "Thane", "Aurangabad", "Solapur", "Kolhapur", "Navi Mumbai"],
    "Manipur": ["Imphal", "Thoubal"],
    "Meghalaya": ["Shillong", "Tura"],
    "Mizoram": ["Aizawl", "Lunglei"],
    "Nagaland": ["Kohima", "Dimapur"],
    "Odisha": ["Bhubaneswar", "Cuttack", "Rourkela", "Berhampur", "Sambalpur", "Puri"],
    "Punjab": ["Ludhiana", "Amritsar", "Jalandhar", "Patiala", "Bathinda", "Mohali"],
    "Rajasthan": ["Jaipur", "Jodhpur", "Udaipur", "Kota", "Ajmer", "Bikaner", "Alwar"],
    "Sikkim": ["Gangtok", "Namchi"],
    "Tamil Nadu": ["Chennai", "Coimbatore", "Madurai", "Tiruchirappalli", "Salem", "Tirunelveli", "Erode", "Vellore", "Tiruppur"],
    "Telangana": ["Hyderabad", "Warangal", "Nizamabad", "Karimnagar", "Khammam", "Ramagundam"],
    "Tripura": ["Agartala", "Udaipur (Tripura)"],
    "Uttar Pradesh": ["Lucknow", "Kanpur", "Varanasi", "Agra", "Prayagraj", "Meerut", "Noida", "Ghaziabad", "Bareilly", "Gorakhpur"],
    "Uttarakhand": ["Dehradun", "Haridwar", "Haldwani", "Rishikesh", "Roorkee"],
    "West Bengal": ["Kolkata", "Howrah", "Durgapur", "Asansol", "Siliguri", "Darjeeling"],
    # Union territories
    "Andaman and Nicobar Islands": ["Port Blair"],
    "Chandigarh": ["Chandigarh"],
    "Dadra and Nagar Haveli and Daman and Diu": ["Silvassa", "Daman"],
    "Delhi": ["New Delhi", "Delhi"],
    "Jammu and Kashmir": ["Srinagar", "Jammu"],
    "Ladakh": ["Leh", "Kargil"],
    "Lakshadweep": ["Kavaratti"],
    "Puducherry": ["Puducherry", "Karaikal"],
}

#: Abbreviations and spellings that people actually type, mapped to the
#: canonical city. Used to match historical free-text values, never to accept
#: new input - new input comes from the picker.
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
    """Collapse a typed place name to a comparison key."""
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def seed(db: Session, tenant_id: str) -> int:
    """Insert any seed location this tenant does not have. Idempotent."""
    existing = {
        (state, city)
        for state, city in db.execute(
            select(Location.state, Location.city).where(Location.tenant_id == tenant_id)
        ).all()
    }

    added = 0
    for state, cities in SEED.items():
        for city in cities:
            if (state, city) in existing:
                continue
            db.add(Location(tenant_id=tenant_id, state=state, city=city))
            added += 1

    if added:
        logger.info("Seeded %d location(s) for %s", added, tenant_id)
    return added


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

"""
Room-sharing matches and the gender policy (addendum B7, open question C2).

The policy is narrow on purpose: an exact match between two stated binary
values, and nothing else. `OTHER` paired with `OTHER` is deliberately refused -
identical labels are not consent, and the fallback costs a room rather than
anything that matters.
"""
from datetime import date

import pytest

from app.core.enums import Designation, Gender, RequestType, Role, TravellerStatus
from app.models.project import Project
from app.models.request import Notification, RequestTraveller, TravelRequest
from app.models.user import User
from app.services import costay

TENANT = "designboxed"


def d(day: int) -> date:
    return date(2026, 10, day)


# ---------------------------------------------------------------------------
# The policy, on its own
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "a,b,allowed",
    [
        (Gender.MALE, Gender.MALE, True),
        (Gender.FEMALE, Gender.FEMALE, True),
        (Gender.MALE, Gender.FEMALE, False),
        (Gender.FEMALE, Gender.MALE, False),
        # Not just cross-gender: an unstated or non-binary value never shares,
        # even with an identical one.
        (Gender.OTHER, Gender.OTHER, False),
        (Gender.UNDISCLOSED, Gender.UNDISCLOSED, False),
        (Gender.OTHER, Gender.MALE, False),
        (Gender.UNDISCLOSED, Gender.FEMALE, False),
        (Gender.MALE, None, False),
    ],
)
def test_may_share_room(a, b, allowed):
    assert costay.may_share_room(a, b) is allowed


# ---------------------------------------------------------------------------
# Matching against real stays
# ---------------------------------------------------------------------------


@pytest.fixture
def world(db):
    project = Project(tenant_id=TENANT, name="Monsoon Survey", code="MON-1")
    db.add(project)

    def person(name, email, gender, designation=None):
        user = User(
            tenant_id=TENANT,
            email=email,
            full_name=name,
            role=Role.GROUND_STAFF,
            gender=gender,
            designation=designation,
            password_hash="x",
        )
        db.add(user)
        return user

    ravi = person("Ravi Kumar", "ravi@designboxed.com", Gender.MALE, Designation.EXECUTIVE)
    arjun = person("Arjun Nair", "arjun@designboxed.com", Gender.MALE, Designation.TEAM_LEAD)
    meera = person("Meera Iyer", "meera@designboxed.com", Gender.FEMALE)
    sam = person("Sam Roy", "sam@designboxed.com", Gender.OTHER)
    db.commit()
    return project, ravi, arjun, meera, sam


def book(db, project, user, *, city="Mumbai", check_in=3, check_out=6,
         status=TravellerStatus.BOOKED, draft=False, cancelled=False, active=True):
    if not active:
        user.is_active = False
    row = TravelRequest(
        tenant_id=TENANT,
        request_type=RequestType.HOTEL,
        project_id=project.id,
        requester_id=user.id,
        hotel_city=city,
        check_in=d(check_in),
        check_out=d(check_out),
        is_draft=draft,
        is_cancelled=cancelled,
    )
    row.travellers = [RequestTraveller(user_id=user.id, status=status)]
    db.add(row)
    db.commit()
    return row


def matches(db, for_user, *, city="Mumbai", check_in=3, check_out=6, exclude=None):
    return costay.find_matches(
        db,
        tenant_id=TENANT,
        for_user=for_user,
        city=city,
        check_in=d(check_in),
        check_out=d(check_out) if check_out else None,
        exclude_request_id=exclude,
    )


def test_a_colleague_of_the_same_gender_in_the_same_city_is_offered(db, world):
    project, ravi, arjun, _, _ = world
    book(db, project, arjun)

    found = matches(db, ravi)
    assert len(found) == 1
    assert found[0].full_name == "Arjun Nair"
    # C2 as built: name and designation are shown. An internal ops tool cannot
    # act on "1 colleague available".
    assert found[0].designation == "TEAM_LEAD"
    assert found[0].overlapping_nights == 3


def test_a_colleague_of_a_different_gender_is_never_offered(db, world):
    project, ravi, _, meera, _ = world
    book(db, project, meera)
    assert matches(db, ravi) == []


def test_a_non_binary_colleague_falls_back_to_a_separate_room(db, world):
    project, ravi, _, _, sam = world
    book(db, project, sam)
    assert matches(db, ravi) == []


def test_two_people_who_both_declined_to_state_are_still_not_paired(db, world):
    project, ravi, _, _, sam = world
    ravi.gender = Gender.OTHER
    db.commit()
    book(db, project, sam)
    assert matches(db, ravi) == []


def test_a_stay_in_another_city_is_not_a_match(db, world):
    project, ravi, arjun, _, _ = world
    book(db, project, arjun, city="Pune")
    assert matches(db, ravi) == []


def test_city_matching_ignores_case_and_padding(db, world):
    project, ravi, arjun, _, _ = world
    book(db, project, arjun, city="  mumbai ")
    assert len(matches(db, ravi)) == 1


def test_dates_that_only_touch_are_not_a_match(db, world):
    """Arjun checks out on the 6th, Ravi checks in on the 6th. No shared night,
    so there is no room to share."""
    project, ravi, arjun, _, _ = world
    book(db, project, arjun, check_in=1, check_out=3)
    assert matches(db, ravi, check_in=3, check_out=6) == []


def test_a_single_shared_night_is_enough(db, world):
    project, ravi, arjun, _, _ = world
    book(db, project, arjun, check_in=5, check_out=9)
    found = matches(db, ravi, check_in=3, check_out=6)
    assert found[0].overlapping_nights == 1


@pytest.mark.parametrize(
    "status,offered",
    [
        (TravellerStatus.PENDING, True),
        (TravellerStatus.APPROVED, True),
        (TravellerStatus.BOOKED, True),
        (TravellerStatus.REJECTED, False),
        (TravellerStatus.CANCELLED, False),
    ],
)
def test_only_live_stays_are_offered(db, world, status, offered):
    """Pending counts as well as booked: by the time both are BOOKED the saving
    has already been missed."""
    project, ravi, arjun, _, _ = world
    book(db, project, arjun, status=status)
    assert bool(matches(db, ravi)) is offered


def test_a_draft_stay_is_never_offered(db, world):
    """A draft is private. Offering to share it would disclose it."""
    project, ravi, arjun, _, _ = world
    book(db, project, arjun, draft=True)
    assert matches(db, ravi) == []


def test_a_cancelled_stay_is_not_offered(db, world):
    project, ravi, arjun, _, _ = world
    book(db, project, arjun, cancelled=True)
    assert matches(db, ravi) == []


def test_someone_who_has_left_is_not_offered(db, world):
    project, ravi, arjun, _, _ = world
    book(db, project, arjun, active=False)
    assert matches(db, ravi) == []


def test_the_requester_is_never_offered_their_own_stay(db, world):
    project, ravi, _, _, _ = world
    book(db, project, ravi)
    assert matches(db, ravi) == []


def test_the_request_being_edited_is_excluded(db, world):
    project, ravi, arjun, _, _ = world
    theirs = book(db, project, arjun)
    assert matches(db, ravi, exclude=theirs.id) == []


def test_matches_are_ordered_by_nights_in_common(db, world):
    project, ravi, arjun, _, _ = world
    deepak = User(
        tenant_id=TENANT,
        email="deepak@designboxed.com",
        full_name="Deepak Shah",
        role=Role.GROUND_STAFF,
        gender=Gender.MALE,
        password_hash="x",
    )
    db.add(deepak)
    db.commit()

    book(db, project, arjun, check_in=5, check_out=7)      # 1 night in common
    book(db, project, deepak, check_in=2, check_out=8)     # 3 nights in common

    found = matches(db, ravi, check_in=3, check_out=6)
    assert [m.full_name for m in found] == ["Deepak Shah", "Arjun Nair"]


def test_a_colleague_on_two_overlapping_stays_is_listed_once(db, world):
    project, ravi, arjun, _, _ = world
    book(db, project, arjun, check_in=3, check_out=6)
    book(db, project, arjun, check_in=4, check_out=7)
    assert len(matches(db, ravi)) == 1


# ---------------------------------------------------------------------------
# The colleague is told (C2)
# ---------------------------------------------------------------------------


def test_asking_to_share_notifies_the_colleague(db, world):
    project, ravi, arjun, _, _ = world
    theirs = book(db, project, arjun)

    costay.notify_share_request(
        db, tenant_id=TENANT, colleague_id=arjun.id, requester=ravi, request=theirs
    )
    db.commit()

    notes = db.query(Notification).all()
    # Two channels, one event: the in-app row and the email attempt are
    # separate facts in the ledger.
    assert {str(n.channel) for n in notes} == {"IN_APP", "EMAIL"}
    assert all(n.user_id == arjun.id for n in notes)
    assert all(n.kind == "COSTAY_REQUESTED" for n in notes)

    in_app = next(n for n in notes if str(n.channel) == "IN_APP")
    assert "Ravi Kumar" in in_app.body
    assert "Mumbai" in in_app.body

    mail = next(n for n in notes if str(n.channel) == "EMAIL")
    assert mail.to_address == arjun.email
    assert mail.subject.startswith("Room sharing request")


def test_the_share_email_is_addressed_and_signed_off(db, world, outbox):
    """The colleague gets a readable message, not a database row pasted into mail."""
    project, ravi, arjun, _, _ = world
    theirs = book(db, project, arjun)

    costay.notify_share_request(
        db, tenant_id=TENANT, colleague_id=arjun.id, requester=ravi, request=theirs
    )
    db.commit()

    assert len(outbox.messages) == 1
    sent = outbox.messages[0]
    assert sent["to"] == arjun.email
    assert sent["body"].startswith("Hello Arjun,")
    assert "Ravi Kumar" in sent["body"]

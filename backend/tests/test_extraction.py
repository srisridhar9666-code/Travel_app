"""
Reading a ticket, and refusing to pretend (addendum B3).

The parser is tested rather than the model. What matters is that every awkward
shape a language model actually produces - a code fence, a sentence before the
JSON, a missing key, a date in the wrong format, a confident empty answer -
either becomes storable fields or becomes an explained failure. What must never
happen is a silent empty booking presented as a successful extraction.
"""
from datetime import date, datetime

from app.services import extraction

GOOD = """{
  "booking_reference": "QK8T2M",
  "carrier": "IndiGo",
  "service_number": "6E-4412",
  "passenger_name": "RAVI KUMAR",
  "origin": "Hyderabad",
  "destination": "Mumbai",
  "depart_at": "2027-03-10T06:45",
  "arrive_at": "2027-03-10T08:20",
  "hotel_name": null,
  "check_in": null,
  "check_out": null,
  "confidence": {
    "booking_reference": 0.98, "carrier": 0.95, "service_number": 0.97,
    "passenger_name": 0.93, "origin": 0.96, "destination": 0.96,
    "depart_at": 0.9, "arrive_at": 0.62
  },
  "document_type": "FLIGHT"
}"""


def test_a_clean_response_becomes_typed_fields():
    result = extraction.parse(GOOD)
    assert result.ok
    assert result.fields["booking_reference"] == "QK8T2M"
    assert result.fields["depart_at"] == datetime(2027, 3, 10, 6, 45)
    assert result.fields["check_in"] is None
    assert result.document_type == "FLIGHT"


def test_low_confidence_fields_are_flagged_for_a_human():
    """The reviewer needs to know where to look first. Arrival came back at 0.62."""
    result = extraction.parse(GOOD)
    assert result.low_confidence_fields == ["arrive_at"]


def test_confidence_is_not_reported_for_fields_that_came_back_empty():
    """A null the model was unsure about is still a null; flagging it would send
    the reviewer hunting for something that is not on the document."""
    raw = """{"booking_reference": "ABC123", "hotel_name": null,
              "confidence": {"booking_reference": 0.9, "hotel_name": 0.1}}"""
    assert extraction.parse(raw).low_confidence_fields == []


def test_a_fenced_response_is_unwrapped():
    """Models fence JSON even when told not to."""
    result = extraction.parse("```json\n" + GOOD + "\n```")
    assert result.ok
    assert result.fields["booking_reference"] == "QK8T2M"


def test_prose_around_the_json_is_tolerated():
    result = extraction.parse(
        "Here is the extracted data:\n" + GOOD + "\nLet me know if you need more."
    )
    assert result.ok
    assert result.fields["carrier"] == "IndiGo"


def test_a_hotel_confirmation_parses_its_own_fields():
    raw = """{"booking_reference": "HTL-99812", "hotel_name": "Taj Santacruz",
              "check_in": "2027-03-10", "check_out": "2027-03-13",
              "carrier": null, "origin": null,
              "confidence": {"booking_reference": 0.99, "check_in": 0.95}}"""
    result = extraction.parse(raw)
    assert result.ok
    assert result.fields["check_in"] == date(2027, 3, 10)
    assert result.fields["check_out"] == date(2027, 3, 13)
    assert result.fields["hotel_name"] == "Taj Santacruz"


def test_missing_keys_are_simply_absent_not_invented():
    result = extraction.parse('{"booking_reference": "X1"}')
    assert result.ok
    assert result.fields["carrier"] is None
    assert result.confidence == {}


def test_a_date_in_an_unexpected_format_becomes_none_rather_than_a_guess():
    """A misread departure time is worse than a blank one: blank gets typed in,
    wrong gets someone to an airport on the wrong day."""
    raw = '{"booking_reference": "X1", "depart_at": "10th March 2027, 6.45am"}'
    result = extraction.parse(raw)
    assert result.ok
    assert result.fields["depart_at"] is None


def test_several_datetime_formats_are_accepted():
    for text, expected in (
        ("2027-03-10T06:45", datetime(2027, 3, 10, 6, 45)),
        ("2027-03-10T06:45:30", datetime(2027, 3, 10, 6, 45, 30)),
        ("2027-03-10 06:45", datetime(2027, 3, 10, 6, 45)),
        ("2027-03-10T06:45Z", datetime(2027, 3, 10, 6, 45)),
    ):
        result = extraction.parse('{"booking_reference": "X", "depart_at": "%s"}' % text)
        assert result.fields["depart_at"] == expected, text


def test_a_non_json_response_is_an_explained_failure():
    result = extraction.parse("I am unable to read this document.")
    assert not result.ok
    assert "not JSON" in result.error
    assert result.raw == "I am unable to read this document."


def test_a_json_array_is_refused():
    result = extraction.parse('["QK8T2M"]')
    assert not result.ok


def test_an_entirely_empty_extraction_is_a_failure_not_a_blank_booking():
    """The failure mode that matters: a form that looks successfully extracted
    but contains nothing, waved through by a tired reviewer."""
    raw = """{"booking_reference": null, "carrier": null, "service_number": null,
              "passenger_name": null, "origin": null, "destination": null,
              "depart_at": null, "arrive_at": null, "hotel_name": null,
              "check_in": null, "check_out": null, "confidence": {}}"""
    result = extraction.parse(raw)
    assert not result.ok
    assert "no fields" in result.error


def test_confidence_outside_zero_to_one_is_clamped():
    raw = '{"booking_reference": "X1", "confidence": {"booking_reference": 4.2}}'
    assert extraction.parse(raw).confidence["booking_reference"] == 1.0

    raw = '{"booking_reference": "X1", "confidence": {"booking_reference": -3}}'
    assert extraction.parse(raw).confidence["booking_reference"] == 0.0


def test_a_non_numeric_confidence_is_dropped_rather_than_coerced():
    raw = '{"booking_reference": "X1", "confidence": {"booking_reference": "high"}}'
    assert extraction.parse(raw).confidence == {}


def test_overlong_values_are_truncated_to_fit_their_columns():
    raw = '{"booking_reference": "%s"}' % ("A" * 400)
    assert len(extraction.parse(raw).fields["booking_reference"]) == 120


def test_whitespace_only_values_become_none():
    raw = '{"booking_reference": "X1", "carrier": "   "}'
    assert extraction.parse(raw).fields["carrier"] is None


def test_numbers_are_accepted_where_text_is_expected():
    """Service numbers come back as bare integers often enough to handle."""
    raw = '{"booking_reference": "X1", "service_number": 4412}'
    assert extraction.parse(raw).fields["service_number"] == "4412"

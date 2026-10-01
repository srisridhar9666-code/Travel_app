"""
Generate the sample tickets the Phase 5 smoke test feeds to the model.

Committed as a generator rather than as binaries so the fixtures can be
regenerated, and so what they claim is readable in the diff rather than hidden
inside a PNG.
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FIXTURES = Path(__file__).resolve().parent / "fixtures"

FLIGHT = [
    ("INDIGO", 34),
    ("Electronic Ticket / Boarding Confirmation", 17),
    ("", 10),
    ("PNR / Booking Reference:   QK8T2M", 22),
    ("Flight:                    6E-4412", 22),
    ("Passenger:                 RAVI KUMAR", 22),
    ("", 10),
    ("From:   Hyderabad (HYD)", 20),
    ("To:     Mumbai (BOM)", 20),
    ("", 10),
    ("Departure:  10 Mar 2027   06:45", 20),
    ("Arrival:    10 Mar 2027   08:20", 20),
    ("", 10),
    ("Seat 14A   |   Economy   |   Baggage 15kg", 16),
    ("Please carry photo identification.", 15),
]

HOTEL = [
    ("TAJ SANTACRUZ", 32),
    ("Booking Confirmation", 17),
    ("", 10),
    ("Confirmation Number:   HTL-99812", 22),
    ("Guest:                 ARJUN NAIR", 22),
    ("", 10),
    ("Hotel:      Taj Santacruz, Mumbai", 20),
    ("Check in:   10 March 2027   after 14:00", 20),
    ("Check out:  13 March 2027   before 11:00", 20),
    ("", 10),
    ("Room: 1 x Deluxe King   |   3 nights", 16),
    ("Breakfast included.", 15),
]

#: A document with nothing a ticket would have on it. The smoke test uses it to
#: prove a failed read is reported rather than waved through as empty fields.
NOT_A_TICKET = [
    ("MEETING NOTES", 30),
    ("", 12),
    ("Discussed the Q2 store rollout.", 20),
    ("Action: Priya to confirm the vendor list.", 20),
    ("No travel decisions were taken.", 20),
]


def render(lines, path: Path, size=(1000, 620)) -> Path:
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    y = 40
    for text, points in lines:
        if text:
            try:
                font = ImageFont.truetype("arial.ttf", points)
            except OSError:
                font = ImageFont.load_default(size=points)
            draw.text((50, y), text, fill="black", font=font)
        y += points + 12
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)
    return path


if __name__ == "__main__":
    for name, lines in (
        ("flight_ticket.png", FLIGHT),
        ("hotel_confirmation.png", HOTEL),
        ("not_a_ticket.png", NOT_A_TICKET),
    ):
        print("wrote", render(lines, FIXTURES / name))

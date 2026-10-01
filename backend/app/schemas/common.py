"""Types shared across the response schemas."""
from datetime import datetime
from typing import Annotated

from pydantic import PlainSerializer

from app.core.clock import utc_iso

#: A moment the server recorded - created, decided, sent, read. Stored as naive
#: UTC; written out with a trailing Z so the browser converts it to India time
#: instead of reading it as a local time and showing it 5h30 early.
#:
#: Not for trip times (`start_at`, `check_in`, a ticket's departure): those are
#: the time on the ticket as a person typed it, and stay exactly as typed.
UTCInstant = Annotated[datetime, PlainSerializer(utc_iso, return_type=str, when_used="json")]

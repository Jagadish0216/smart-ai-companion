"""Publication date parsing; never infer dates from retrieval or titles."""
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime


def parse_publication_date(value):
    if isinstance(value, datetime):
        try:
            return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
        except (ValueError, OverflowError):
            return None
    if not isinstance(value, str) or len(value) > 100:
        return None
    value = value.strip()
    try:
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
        except (ValueError, TypeError, OverflowError):
            date = None
        if date is None:
            for fmt in ("%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%b %d %Y", "%d %B %Y", "%d %b %Y", "%Y-%m-%d %H:%M:%S%z",
                        "%Y/%m/%d", "%d %b %Y %H:%M:%S %z", "%B %d, %Y %I:%M %p %z"):
                try:
                    date = datetime.strptime(value, fmt)
                    break
                except ValueError:
                    pass
    if date is None:
        return None
    try:
        return (date if date.tzinfo else date.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None


def consistent_date(values):
    dates = [parse_publication_date(v) for v in values]
    dates = [d for d in dates if d is not None]
    # Different precision within the same day is allowed; choose the oldest
    # value, so date-only metadata cannot manufacture recent intraday evidence.
    if not dates or len({d.date() for d in dates}) != 1:
        return None
    return min(dates)

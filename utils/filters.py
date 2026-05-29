"""utils/filters.py — Jinja2 template filters."""
from datetime import datetime as dt


def _parse_timestamp(value):
    try:
        if isinstance(value, str):
            value = float(value)
        if value > 1e12:
            value /= 1000
        return dt.fromtimestamp(value)
    except (ValueError, TypeError):
        return None


def register_filters(app):
    @app.template_filter("datetimeformat")
    def datetimeformat(value):
        d = _parse_timestamp(value)
        return d.strftime("%b %d, %H:%M") if d else "Unknown time"

    @app.template_filter("timestamp_to_datetime")
    def timestamp_to_datetime_filter(value):
        return _parse_timestamp(value) or dt.now()

    @app.template_filter("format_datetime")
    def format_datetime_filter(value):
        if isinstance(value, (int, float, str)):
            value = _parse_timestamp(value)
        if not isinstance(value, dt):
            return "Unknown time"
        diff = dt.now() - value
        if diff.days == 0: return value.strftime("%H:%M")
        if diff.days == 1: return "Yesterday"
        if diff.days < 7:  return value.strftime("%A")
        return value.strftime("%b %d")

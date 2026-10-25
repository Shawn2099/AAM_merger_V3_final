"""Display times are shown in IST; the database stays naive UTC."""

from __future__ import annotations

from datetime import datetime

from app.api.templating import IST, ist, templates


def test_naive_value_is_read_as_utc_and_shown_in_ist():
    # 2026-10-25 12:00 UTC is 17:30 IST.
    assert ist(datetime(2026, 10, 25, 12, 0)) == "2026-10-25 17:30 IST"


def test_aware_value_is_converted_to_ist():
    from datetime import UTC

    assert ist(datetime(2026, 10, 25, 12, 0, tzinfo=UTC)) == "2026-10-25 17:30 IST"


def test_none_renders_as_dash():
    assert ist(None) == "—"


def test_custom_format_keeps_seconds_and_zone_label():
    assert ist(datetime(2026, 1, 1, 0, 0), "%d %b %Y, %H:%M:%S") == "01 Jan 2026, 05:30:00 IST"


def test_ist_offset_is_five_thirty():
    assert IST.utcoffset(None).total_seconds() == 5.5 * 3600


def test_filter_is_registered_on_the_shared_templates():
    assert templates.env.filters["ist"] is ist


def test_template_renders_through_the_filter():
    out = templates.env.from_string("{{ t | ist('%H:%M') }}").render(
        t=datetime(2026, 10, 25, 12, 0)
    )
    assert out == "17:30 IST"

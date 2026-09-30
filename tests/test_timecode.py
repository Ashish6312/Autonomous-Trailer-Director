import pytest

from trailer_director.domain.timecode import format_timecode, parse_timecode


@pytest.mark.parametrize(
    ("text", "millis"),
    [("00:00:00.000", 0), ("00:01:05.250", 65_250), ("01:00:00.001", 3_600_001)],
)
def test_parse_and_format_round_trip(text, millis):
    assert parse_timecode(text) == millis
    assert format_timecode(millis) == text


@pytest.mark.parametrize("text", ["0:00:01.000", "00:00:60.000", "00:00:01", "00:00:01.5", "", 12])
def test_parse_rejects_malformed_values(text):
    with pytest.raises(ValueError):
        parse_timecode(text)

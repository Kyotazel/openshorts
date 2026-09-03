import pytest

from source_clock import BadClock, parse_clock, parse_youtube_t


def test_clock_table():
    assert parse_clock("12:45") == 765
    assert parse_clock("1:12:45") == 4365
    assert parse_clock("765") == 765
    assert parse_clock("0:00") == 0
    assert parse_clock("0") == 0


@pytest.mark.parametrize("bad", ["", "12.45", "abc", "99:99", "1:2:3:4"])
def test_clock_rejects(bad):
    with pytest.raises(BadClock):
        parse_clock(bad)


def test_youtube_t():
    assert parse_youtube_t("https://youtu.be/x?t=45") == 45
    assert parse_youtube_t("https://www.youtube.com/watch?v=x&t=765") == 765
    assert parse_youtube_t("https://www.youtube.com/watch?v=x&t=1h2m3s") == 3723
    assert parse_youtube_t("https://www.youtube.com/watch?v=x") is None

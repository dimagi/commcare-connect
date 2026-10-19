import pytest

from commcare_connect.utils.file import neutralize_formula


@pytest.mark.parametrize(
    "value, expected",
    [
        ("=HYPERLINK(1)", "'=HYPERLINK(1)"),
        ("+254 722 118 340", "'+254 722 118 340"),
        ("-1", "'-1"),
        ("@SUM(A1)", "'@SUM(A1)"),
        ("\tcmd", "'\tcmd"),
        ("\rcmd", "'\rcmd"),
        ("Grace Wanjiru", "Grace Wanjiru"),
        ("", ""),
        (None, None),
        (42, 42),
    ],
)
def test_neutralize_formula(value, expected):
    assert neutralize_formula(value) == expected

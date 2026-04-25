import pytest

from social_info.url_utils import canonical_url


@pytest.mark.parametrize(
    "input_url,expected",
    [
        (
            "https://example.com/a?utm_source=x&utm_medium=email&id=1",
            "https://example.com/a?id=1",
        ),
        ("https://example.com/a?fbclid=abc123", "https://example.com/a"),
        ("https://example.com/a?ref=hn", "https://example.com/a"),
        ("https://example.com/a?source=rss", "https://example.com/a"),
        (
            "https://example.com/a?utm_campaign=x&keep=1&utm_term=y",
            "https://example.com/a?keep=1",
        ),
        ("https://example.com/a/", "https://example.com/a"),
        ("HTTPS://Example.COM/A?ID=1", "https://example.com/A?ID=1"),
        ("https://example.com/a#frag", "https://example.com/a"),
        ("https://example.com/a?b=2&a=1", "https://example.com/a?a=1&b=2"),
    ],
)
def test_canonical_url(input_url, expected):
    assert canonical_url(input_url) == expected

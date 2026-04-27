import pytest

from social_info.pipeline import _redact_secrets


@pytest.mark.parametrize(
    "input_str,expected",
    [
        (
            "HTTPStatusError: Client error '401' for url 'https://api.apify.com/v2/acts/x/run-sync?token=apify_api_LEAKED'",
            "HTTPStatusError: Client error '401' for url 'https://api.apify.com/v2/acts/x/run-sync?token=***'",
        ),
        (
            "https://api.apify.com/v2/acts/x/run-sync?token=apify_api_LEAKED&maxItems=10",
            "https://api.apify.com/v2/acts/x/run-sync?token=***&maxItems=10",
        ),
        (
            "https://example.com/api?api_key=SECRET_KEY",
            "https://example.com/api?api_key=***",
        ),
        (
            "https://example.com/api?apikey=SECRET",
            "https://example.com/api?apikey=***",
        ),
        (
            "https://example.com/api?access_token=SECRET",
            "https://example.com/api?access_token=***",
        ),
        (
            "https://example.com/api?Token=MIXED_CASE",
            "https://example.com/api?Token=***",
        ),
        (
            "no secrets here, plain error",
            "no secrets here, plain error",
        ),
        (
            "https://www.reddit.com/r/x/top.json?t=day&limit=10",
            "https://www.reddit.com/r/x/top.json?t=day&limit=10",
        ),
        (
            "https://example.com/a?token=A&other=B&token=C",
            "https://example.com/a?token=***&other=B&token=***",
        ),
    ],
)
def test_redact_secrets(input_str: str, expected: str) -> None:
    assert _redact_secrets(input_str) == expected

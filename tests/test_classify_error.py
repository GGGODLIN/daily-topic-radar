"""Test pipeline.classify_error error class taxonomy."""
from unittest.mock import MagicMock

import httpx
import pytest

from social_info.pipeline import classify_error


def _http_status_error(code: int) -> httpx.HTTPStatusError:
    response = MagicMock(spec=httpx.Response)
    response.status_code = code
    return httpx.HTTPStatusError("status", request=MagicMock(), response=response)


@pytest.mark.parametrize(
    "exc,expected",
    [
        (httpx.ReadTimeout("read timeout"), "transient"),
        (httpx.ConnectTimeout("connect timeout"), "transient"),
        (httpx.ConnectError("connect"), "transient"),
        (httpx.ReadError("read"), "transient"),
        (httpx.RemoteProtocolError("proto"), "transient"),
        (_http_status_error(500), "transient"),
        (_http_status_error(502), "transient"),
        (_http_status_error(503), "transient"),
        (_http_status_error(504), "transient"),
        (_http_status_error(401), "user_action_required"),
        (_http_status_error(403), "user_action_required"),
        (_http_status_error(400), "persistent_error"),
        (_http_status_error(404), "persistent_error"),
        (_http_status_error(429), "persistent_error"),
        (ValueError("random non-http exception"), "persistent_error"),
    ],
)
def test_classify_error(exc: BaseException, expected: str) -> None:
    assert classify_error(exc) == expected

import pytest

from social_info.fetchers.relay import apify_post_url, is_valid_relay_url


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8317",
        "http://127.0.0.1:80",
        "http://127.0.0.1:65535",
    ],
)
def test_is_valid_relay_url_accepts_bare_loopback(url):
    assert is_valid_relay_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1:8317",
        "http://127.0.0.1",
        "http://127.0.0.1:0",
        "http://127.0.0.1:65536",
        "http://127.0.0.1:8317/",
        "http://127.0.0.1:8317/path",
        "http://127.0.0.1:8317?x=1",
        "http://127.0.0.1:8317#frag",
        "http://127.0.0.1:8317:bad",
        "http://user@127.0.0.1:8317",
        "http://127.0.0.1:8317/path#frag?x=1",
        "http://localhost:8317",
        "http://10.0.0.2:8317",
        "http://[::1]:8317",
        "http://127.0.0.2:8317",
        "http://192.168.1.1:8317",
        "ftp://127.0.0.1:8317",
        "ws://127.0.0.1:8317",
        "127.0.0.1:8317",
        "",
        "not-a-url",
    ],
)
def test_is_valid_relay_url_rejects_invalid(url):
    assert not is_valid_relay_url(url)


def test_apify_post_url_direct_token(monkeypatch):
    monkeypatch.setenv("APIFY_RELAY_URL", "")
    monkeypatch.setenv("APIFY_TOKEN_TWITTER", "fake-token")
    url, params = apify_post_url("https://api.apify.com/endpoint", "APIFY_TOKEN_TWITTER")
    assert url == "https://api.apify.com/endpoint"
    assert params == {"token": "fake-token"}

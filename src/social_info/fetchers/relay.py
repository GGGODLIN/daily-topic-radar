import os
from urllib.parse import urlsplit


def is_valid_relay_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if parts.scheme != "http":
        return False
    if parts.username is not None or parts.password is not None:
        return False
    if parts.hostname != "127.0.0.1":
        return False
    if parts.path != "":
        return False
    if parts.query or parts.fragment:
        return False
    try:
        port = parts.port
    except ValueError:
        return False
    return port is not None and 0 < port <= 65535


def apify_post_url(endpoint: str, token_env_key: str) -> tuple[str, dict | None]:
    relay = os.environ.get("APIFY_RELAY_URL", "")
    if relay:
        if not is_valid_relay_url(relay):
            raise RuntimeError("invalid APIFY_RELAY_URL")
        return f"{relay}{urlsplit(endpoint).path}", None
    token = os.environ.get(token_env_key)
    if not token:
        raise RuntimeError(f"{token_env_key} env var not set")
    return endpoint, {"token": token}

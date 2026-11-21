"""URL canonicalization for dedup."""
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_PARAM_PREFIXES = ("utm_",)
TRACKING_PARAM_NAMES = frozenset({
    "fbclid", "gclid", "msclkid", "yclid",
    "ref", "ref_src", "ref_url", "source",
    "mc_cid", "mc_eid", "_hsenc", "_hsmi",
})

_DEFAULT_PORTS = {"http": 80, "https": 443}


def _strip_default_port(netloc: str, scheme: str, port: int | None) -> str:
    default_port = _DEFAULT_PORTS.get(scheme)
    if default_port is None or port != default_port:
        return netloc
    userinfo, sep, hostport = netloc.rpartition("@")
    host_only, colon, _ = hostport.rpartition(":")
    if not colon:
        return netloc
    return f"{userinfo}{sep}{host_only}"


def canonical_url(url: str) -> str:
    """Strip tracking params, normalize host, sort remaining params, drop fragment."""
    if not isinstance(url, str):
        return ""
    cleaned = url.strip()
    if not cleaned:
        return ""
    try:
        parts = urlsplit(cleaned)
        port = parts.port
    except ValueError:
        return cleaned

    scheme = parts.scheme.lower()
    netloc = _strip_default_port(parts.netloc.lower(), scheme, port)
    if parts.path == "" and netloc:
        path = "/"
    elif parts.path and parts.path != "/" and parts.path.endswith("/"):
        path = parts.path.rstrip("/")
    else:
        path = parts.path

    kept = []
    for k, v in parse_qsl(parts.query, keep_blank_values=True):
        kl = k.lower()
        if any(kl.startswith(p) for p in TRACKING_PARAM_PREFIXES):
            continue
        if kl in TRACKING_PARAM_NAMES:
            continue
        kept.append((k, v))
    kept.sort()
    query = urlencode(kept)

    return urlunsplit((scheme, netloc, path, query, ""))

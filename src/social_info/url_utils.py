"""URL canonicalization for dedup."""
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_PARAM_PREFIXES = ("utm_",)
TRACKING_PARAM_NAMES = frozenset({
    "fbclid", "gclid", "msclkid", "yclid",
    "ref", "ref_src", "ref_url", "source",
    "mc_cid", "mc_eid", "_hsenc", "_hsmi",
})


def canonical_url(url: str) -> str:
    """Strip tracking params, normalize host, sort remaining params, drop fragment."""
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    if parts.path and parts.path != "/" and parts.path.endswith("/"):
        path = parts.path.rstrip("/")
    else:
        path = parts.path

    kept = []
    for k, v in parse_qsl(parts.query, keep_blank_values=True):
        if any(k.startswith(p) for p in TRACKING_PARAM_PREFIXES):
            continue
        if k in TRACKING_PARAM_NAMES:
            continue
        kept.append((k, v))
    kept.sort()
    query = urlencode(kept)

    return urlunsplit((scheme, netloc, path, query, ""))

"""Shared "collapse a URL down to just scheme+host" primitive - some built-in
forwarding presets (webui/forwarders/presets.py) embed an access token
directly in the URL path, and neither the forwarding log nor a debug export
should ever surface that.
"""

from urllib.parse import urlsplit


def url_origin(url: str) -> str | None:
    """scheme://host from url, dropping path/query/fragment - or None if url
    doesn't parse as an absolute URL at all."""
    parsed = urlsplit(url)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return None

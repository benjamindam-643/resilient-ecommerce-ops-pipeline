"""SSRF-guarded 3PL Logistics API Adapter.

Provides resilient tracking queries and dispatch creation with strict origin
validation and retry policies.
"""
from __future__ import annotations

import logging
from urllib.parse import urlparse
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

DEFAULT_API_BASE = "https://services.giaohangtietkiem.vn"
DEFAULT_TIMEOUT_SEC = 20

# Host allowlist against Server-Side Request Forgery (SSRF)
_ALLOWED_HOST_SUFFIXES = (".giaohangtietkiem.vn",)


def _is_host_allowed(api_base: str) -> bool:
    try:
        parsed = urlparse((api_base or "").strip())
    except Exception:
        return False
    if parsed.scheme != "https":
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    return host == "giaohangtietkiem.vn" or any(host.endswith(s) for s in _ALLOWED_HOST_SUFFIXES)


def create_resilient_session(total_retries: int = 5, backoff_factor: float = 0.5) -> requests.Session:
    session = requests.Session()
    retry_strategy = Retry(
        total=total_retries,
        connect=total_retries,
        read=total_retries,
        status=total_retries,
        status_forcelist=[408, 429, 500, 502, 503, 504],
        backoff_factor=backoff_factor,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    return session


_session = create_resilient_session()


def get_tracking_status(
    tracking_code: str,
    api_token: str,
    api_base: str = DEFAULT_API_BASE,
) -> dict:
    if not _is_host_allowed(api_base):
        raise ValueError(f"Target host rejected by SSRF guard: {api_base!r}")

    url = f"{api_base.rstrip('/')}/services/shipment/v2/{tracking_code.strip()}"
    headers = {"Token": api_token.strip(), "Content-Type": "application/json"}

    response = _session.get(url, headers=headers, timeout=DEFAULT_TIMEOUT_SEC)
    response.raise_for_status()
    return response.json()


"""Reliable, cross-process Google Sheets HTTP client for data sync jobs.

All marketplace subprocesses normally use the same Google identity.  A
per-process retry alone is therefore insufficient: ten processes can still
retry at the same instant and keep hitting the shared quota.  This module
spaces request starts through a tiny file-based critical section and retries
only errors that are safe to repeat.
"""
from __future__ import annotations

import contextlib
import os
import random
import threading
import time
import uuid
from collections.abc import Iterator, Mapping
from typing import Any

import requests
from google.auth.exceptions import TransportError as GoogleAuthTransportError
from google.auth.transport.requests import Request as GoogleAuthRequest
from gspread.exceptions import APIError
from gspread.http_client import HTTPClient


_TRANSIENT_STATUS_CODES = {408, 429, 500, 502, 503, 504}
_QUOTA_REASONS = {
    "rateLimitExceeded",
    "userRateLimitExceeded",
    "quotaExceeded",
    "sharingRateLimitExceeded",
}
_THROTTLE_THREAD_LOCK = threading.Lock()
_FILE_IO_ATTEMPTS = 10
_FILE_IO_RETRY_SECONDS = 0.05


def _float_env(name: str, default: float, *, minimum: float = 0.0, maximum: float | None = None) -> float:
    try:
        value = float((os.getenv(name) or "").strip() or default)
    except ValueError:
        value = default
    value = max(minimum, value)
    return min(value, maximum) if maximum is not None else value


def _int_env(name: str, default: int, *, minimum: int = 1, maximum: int | None = None) -> int:
    try:
        value = int((os.getenv(name) or "").strip() or default)
    except ValueError:
        value = default
    value = max(minimum, value)
    return min(value, maximum) if maximum is not None else value


def _runtime_dir() -> str:
    return (os.getenv("DATA_GG_RUNTIME_DIR") or "").strip() or os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        ".runtime",
    )


def _rate_paths() -> tuple[str, str]:
    runtime_dir = _runtime_dir()
    return (
        os.path.join(runtime_dir, "google_sheets_rate.lock"),
        os.path.join(runtime_dir, "google_sheets_rate.timestamp"),
    )


def _oauth_refresh_lock_path() -> str:
    return os.path.join(_runtime_dir(), "google_oauth_refresh.lock")


def _read_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as source:
            return source.read().strip()
    except (FileNotFoundError, OSError):
        return ""


@contextlib.contextmanager
def _interprocess_file_lock(lock_path: str, timeout: float, label: str) -> Iterator[None]:
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    deadline = time.monotonic() + timeout
    lock_file = open(lock_path, "a+b")
    lock_file.seek(0, os.SEEK_END)
    if lock_file.tell() == 0:
        lock_file.write(b"0")
        lock_file.flush()

    if os.name == "nt":
        import msvcrt

        def acquire() -> None:
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)

        def release() -> None:
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        def acquire() -> None:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

        def release() -> None:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    acquired = False
    try:
        while True:
            try:
                acquire()
                acquired = True
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"{label} timeout after {timeout:.1f}s")
                time.sleep(0.02)
        yield
    finally:
        try:
            if acquired:
                release()
        finally:
            lock_file.close()


@contextlib.contextmanager
def _interprocess_rate_lock(_interval: float) -> Iterator[None]:
    lock_path, _ = _rate_paths()
    timeout = _float_env("DATA_GG_GSHEET_LOCK_TIMEOUT", 10.0, minimum=1.0, maximum=60.0)
    with _interprocess_file_lock(lock_path, timeout, "Google Sheets rate lock"):
        yield


@contextlib.contextmanager
def _interprocess_oauth_refresh_lock() -> Iterator[None]:
    timeout = _float_env(
        "DATA_GG_GOOGLE_OAUTH_LOCK_TIMEOUT",
        180.0,
        minimum=10.0,
        maximum=600.0,
    )
    with _interprocess_file_lock(
        _oauth_refresh_lock_path(),
        timeout,
        "Google OAuth refresh lock",
    ):
        yield


def _write_last_start(path: str, started_at: float) -> None:
    temporary_path = f"{path}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp"
    try:
        with open(temporary_path, "w", encoding="utf-8") as target:
            target.write(f"{started_at:.9f}")
            target.flush()
            os.fsync(target.fileno())
        for attempt in range(_FILE_IO_ATTEMPTS):
            try:
                os.replace(temporary_path, path)
                return
            except PermissionError:
                if attempt + 1 >= _FILE_IO_ATTEMPTS:
                    raise
                time.sleep(_FILE_IO_RETRY_SECONDS * (attempt + 1))
    finally:
        try:
            os.remove(temporary_path)
        except FileNotFoundError:
            pass


def _throttle_google_request() -> None:
    """Space request starts across every data-gg subprocess on this machine."""
    interval = _float_env("DATA_GG_GSHEET_MIN_INTERVAL", 0.25, minimum=0.0, maximum=5.0)
    if interval <= 0:
        return

    _, timestamp_path = _rate_paths()
    with _THROTTLE_THREAD_LOCK:
        with _interprocess_rate_lock(interval):
            try:
                last_start = float(_read_text(timestamp_path) or "0")
            except ValueError:
                last_start = 0.0
            wait_for = min(interval, (last_start + interval) - time.time())
            if wait_for > 0:
                time.sleep(wait_for)
            _write_last_start(timestamp_path, time.time())


def _quota_related_403(error: Mapping[str, Any]) -> bool:
    if error.get("status") == "RESOURCE_EXHAUSTED":
        return True
    errors = error.get("errors")
    if not isinstance(errors, list):
        return False
    for detail in errors:
        if not isinstance(detail, Mapping):
            continue
        if detail.get("domain") == "usageLimits" or detail.get("reason") in _QUOTA_REASONS:
            return True
    return False


def _request_is_idempotent(args: tuple[Any, ...], kwargs: dict[str, Any]) -> bool:
    method = str(args[0] if args else kwargs.get("method") or "").upper()
    endpoint = str(args[1] if len(args) > 1 else kwargs.get("endpoint") or "")
    if method in {"GET", "HEAD", "OPTIONS", "PUT", "DELETE"}:
        return True
    if method != "POST":
        return False
    return (
        endpoint.endswith("/values:batchUpdate")
        or endpoint.endswith("/values:batchClear")
        or endpoint.endswith(":clear")
    )


def _retryable_api_error(error: APIError, *, idempotent: bool) -> bool:
    if error.code == 429 or (error.code == 403 and _quota_related_403(error.error)):
        return True
    return idempotent and error.code in (_TRANSIENT_STATUS_CODES - {429})


def _retry_delay(attempt: int, error: APIError | None = None) -> float:
    maximum = _float_env("DATA_GG_GSHEET_RETRY_MAX_SLEEP", 30.0, minimum=0.0, maximum=300.0)
    if error is not None:
        retry_after = error.response.headers.get("Retry-After")
        if retry_after:
            try:
                return min(maximum, max(0.0, float(retry_after)))
            except ValueError:
                pass
    base = _float_env("DATA_GG_GSHEET_RETRY_BACKOFF", 1.0, minimum=0.0, maximum=60.0)
    jitter = random.uniform(
        0.0,
        _float_env("DATA_GG_GSHEET_RETRY_JITTER", 0.25, minimum=0.0, maximum=5.0),
    )
    return min(maximum, base * (2 ** max(0, attempt - 1)) + jitter)


def _retry_sleep(seconds: float) -> None:
    time.sleep(max(0.0, seconds))


def _request_timeout() -> tuple[float, float]:
    """Bound both connect and read time for every Google Sheets request.

    gspread's ``HTTPClient`` passes ``timeout=None`` explicitly to
    ``AuthorizedSession``.  That bypasses the general requests monkey patch,
    leaving a stalled Google endpoint able to block a sync worker forever.
    """
    timeout = _float_env("DATA_GG_GSHEET_HTTP_TIMEOUT", 45.0, minimum=5.0, maximum=300.0)
    return timeout, timeout


def _oauth_request_timeout() -> tuple[float, float]:
    timeout = _float_env("DATA_GG_GOOGLE_OAUTH_TIMEOUT", 20.0, minimum=5.0, maximum=120.0)
    return timeout, timeout


def _ensure_google_credentials(auth: Any) -> None:
    """Refresh credentials once per process without an OAuth thundering herd."""
    if auth is None or bool(getattr(auth, "valid", False)):
        return

    attempts = _int_env("DATA_GG_GOOGLE_OAUTH_ATTEMPTS", 5, minimum=1, maximum=10)
    with _interprocess_oauth_refresh_lock():
        if bool(getattr(auth, "valid", False)):
            return
        for attempt in range(1, attempts + 1):
            try:
                with requests.Session() as session:
                    transport = GoogleAuthRequest(session=session)

                    def bounded_request(*args: Any, **kwargs: Any):  # noqa: ANN202
                        kwargs.setdefault("timeout", _oauth_request_timeout())
                        return transport(*args, **kwargs)

                    auth.refresh(bounded_request)
                return
            except (requests.exceptions.RequestException, GoogleAuthTransportError) as error:
                if attempt >= attempts:
                    raise
                delay = _retry_delay(attempt)
                print(
                    "[data-gg] Google OAuth retry "
                    f"network_error={type(error).__name__} "
                    f"attempt={attempt + 1}/{attempts} after={delay:.1f}s",
                    flush=True,
                )
                _retry_sleep(delay)


class ResilientHTTPClient(HTTPClient):
    """gspread client with safe retry and a shared request-start limiter."""

    def __init__(self, auth: Any, session: requests.Session | None = None) -> None:
        super().__init__(auth, session=session)
        # HTTPClient defaults to None, which is an explicit infinite timeout
        # when handed to AuthorizedSession.request().
        self.timeout = _request_timeout()

    def request(self, *args: Any, **kwargs: Any):  # noqa: ANN201
        attempts = _int_env("DATA_GG_GSHEET_HTTP_ATTEMPTS", 5, minimum=1, maximum=10)
        idempotent = _request_is_idempotent(args, kwargs)
        _ensure_google_credentials(getattr(self, "auth", None))
        for attempt in range(1, attempts + 1):
            _throttle_google_request()
            try:
                return super().request(*args, **kwargs)
            except APIError as error:
                if attempt >= attempts or not _retryable_api_error(error, idempotent=idempotent):
                    raise
                try:
                    error.response.close()
                except Exception:
                    pass
                delay = _retry_delay(attempt, error)
                print(
                    "[data-gg] Google Sheets retry "
                    f"status={error.code} attempt={attempt + 1}/{attempts} after={delay:.1f}s",
                    flush=True,
                )
                _retry_sleep(delay)
            except (requests.exceptions.RequestException, GoogleAuthTransportError) as error:
                if attempt >= attempts or not idempotent:
                    raise
                delay = _retry_delay(attempt)
                print(
                    "[data-gg] Google Sheets retry "
                    f"network_error={type(error).__name__} "
                    f"attempt={attempt + 1}/{attempts} after={delay:.1f}s",
                    flush=True,
                )
                _retry_sleep(delay)

        raise RuntimeError("Google Sheets request exhausted retries")

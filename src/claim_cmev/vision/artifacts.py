"""Reading evidence and derived artifacts from the object store, for the vision workers."""
from __future__ import annotations

from typing import Any

from ..messaging.consumer import PermanentError, TransientError


def storage_key(object_uri: str) -> str:
    if object_uri.startswith("s3://"):
        return object_uri.split("/", 3)[-1]
    return object_uri.removeprefix("file://local-evidence/")


def is_missing(exc: BaseException) -> bool:
    """Whether a storage error says the object does not exist (botocore carries the S3 code in ``response``)."""
    response = getattr(exc, "response", None)
    s3_code = response.get("Error", {}).get("Code") if isinstance(response, dict) else None
    return isinstance(exc, FileNotFoundError) or s3_code in ("NoSuchKey", "404", "NotFound")


def read_object(storage: Any, object_uri: str, what: str) -> bytes:
    """Bytes by URI. An absent object is final; any other storage failure is worth a retry."""
    try:
        return storage.read(storage_key(object_uri))
    except Exception as exc:  # noqa: BLE001 - classified below
        if is_missing(exc):
            raise PermanentError("artifact_missing", f"{what} is not in the object store") from exc
        raise TransientError("artifact_read_failed", f"could not read {what}: {type(exc).__name__}") from exc


__all__ = ["is_missing", "read_object", "storage_key"]

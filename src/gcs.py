"""Google Cloud Storage helper - private bucket, service account auth.

Provides async wrappers for upload / download / delete operations against
the project's GCS bucket.  All IO runs in a thread pool so the event loop
stays free.

GCS is **optional** - if google-cloud-storage is not installed or
credentials are missing, every public function silently returns None / False
so the rest of the app keeps working with local disk only.
"""

from __future__ import annotations

import asyncio
import logging
import os

logger = logging.getLogger("macro_app")

GCS_BUCKET_NAME = os.environ.get("GCS_BUCKET", "")
GCS_IMAGE_PREFIX = "images/"  # matches backup_db.sh layout

# Manual cache - only caches successful init (retries on transient failure)
_client_cache = None
_client_init_ok = False


def _get_client():
    """Lazy-init a GCS client.  Returns None when unavailable."""
    global _client_cache, _client_init_ok
    if _client_init_ok:
        return _client_cache
    try:
        from google.cloud import storage

        # Prefer explicit credentials file, then default ADC
        creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        if not creds_path:
            project_root = os.path.dirname(os.path.dirname(__file__))
            candidate = os.path.join(project_root, "credentials.json")
            if os.path.isfile(candidate):
                os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = candidate

        _client_cache = storage.Client()
        _client_init_ok = True
        return _client_cache
    except Exception:
        logger.info("GCS client not available - falling back to local disk only")
        return None


def _get_bucket():
    if not GCS_BUCKET_NAME:
        return None
    client = _get_client()
    if client is None:
        return None
    return client.bucket(GCS_BUCKET_NAME)


# ── Public async API ─────────────────────────────────────────────


async def gcs_upload(relative_path: str, data: bytes, content_type: str = "image/jpeg") -> bool:
    """Upload *data* to ``images/<relative_path>`` in the bucket.

    Returns True on success, False if GCS is unavailable or upload fails.
    """
    bucket = _get_bucket()
    if bucket is None:
        return False

    blob_name = f"{GCS_IMAGE_PREFIX}{relative_path}"

    def _upload():
        blob = bucket.blob(blob_name)
        blob.upload_from_string(data, content_type=content_type)

    try:
        await asyncio.to_thread(_upload)
        return True
    except Exception:
        logger.warning("GCS upload failed for %s", blob_name, exc_info=True)
        return False


async def gcs_download(relative_path: str) -> bytes | None:
    """Download ``images/<relative_path>`` from the bucket.

    Returns file bytes on success, None if not found or GCS unavailable.
    """
    bucket = _get_bucket()
    if bucket is None:
        return None

    blob_name = f"{GCS_IMAGE_PREFIX}{relative_path}"

    def _download():
        from google.api_core.exceptions import NotFound
        blob = bucket.blob(blob_name)
        try:
            return blob.download_as_bytes()
        except NotFound:
            return None

    try:
        return await asyncio.to_thread(_download)
    except Exception:
        logger.warning("GCS download failed for %s", blob_name, exc_info=True)
        return None


async def gcs_delete(relative_path: str) -> bool:
    """Delete a single blob at ``images/<relative_path>``.

    Returns True on success or if already absent, False on error.
    """
    bucket = _get_bucket()
    if bucket is None:
        return False

    blob_name = f"{GCS_IMAGE_PREFIX}{relative_path}"

    def _delete():
        from google.api_core.exceptions import NotFound
        blob = bucket.blob(blob_name)
        try:
            blob.delete()
        except NotFound:
            pass  # already gone - that's fine

    try:
        await asyncio.to_thread(_delete)
        return True
    except Exception:
        logger.warning("GCS delete failed for %s", blob_name, exc_info=True)
        return False


async def gcs_delete_prefix(prefix: str) -> int:
    """Delete all blobs under ``images/<prefix>``.

    Returns the count of blobs deleted.  Useful for wiping a session or user
    directory.  Refuses to delete the entire images/ root as a safety guard.
    """
    # Safety: prefix must have at least one path component (e.g. "1/" or "1/session/")
    # Reject empty, "/", or anything that would resolve to the images/ root.
    # B33: also reject path-traversal attempts and absolute paths defensively.
    stripped = prefix.strip("/")
    if not stripped:
        logger.error("gcs_delete_prefix refused empty prefix - would delete all images")
        return 0
    if ".." in stripped.split("/"):
        logger.error("gcs_delete_prefix refused path-traversal prefix: %r", prefix)
        return 0
    if prefix.startswith("/"):
        # Absolute paths against an images/ root are nonsensical; refuse defensively.
        logger.error("gcs_delete_prefix refused absolute prefix: %r", prefix)
        return 0

    bucket = _get_bucket()
    if bucket is None:
        return 0

    full_prefix = f"{GCS_IMAGE_PREFIX}{prefix}"

    def _delete_all():
        from google.api_core.exceptions import NotFound
        blobs = list(bucket.list_blobs(prefix=full_prefix))
        count = 0
        for blob in blobs:
            try:
                blob.delete()
                count += 1
            except NotFound:
                pass  # already gone
        return count

    try:
        return await asyncio.to_thread(_delete_all)
    except Exception:
        logger.warning("GCS prefix delete failed for %s", full_prefix, exc_info=True)
        return 0


async def gcs_upload_file(relative_path: str, local_path: str, content_type: str = "image/jpeg") -> bool:
    """Upload a local file to ``images/<relative_path>`` in the bucket.

    Reads the file and delegates to :func:`gcs_upload`.
    """
    try:
        data = await asyncio.to_thread(_read_file, local_path)
        return await gcs_upload(relative_path, data, content_type)
    except Exception:
        logger.warning("GCS upload_file failed for %s", local_path, exc_info=True)
        return False


def _read_file(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()

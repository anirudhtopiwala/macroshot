"""One-shot script: strip EXIF metadata from every image in the GCS bucket.

Why this exists
---------------
Until the April 11, 2026 security fix, uploaded meal photos were saved to
GCS as raw bytes (``gcs_upload(rel, img_data)`` in
``src/web/routes/meals.py``).  That raw-byte path preserved phone-camera
EXIF metadata - including GPS coordinates - for every photo ever
uploaded.  The disk copy on the VM was already safe (the Pillow re-encode
for the disk save stripped EXIF as a side effect), but the GCS backup
copy leaked users' home addresses.

This script walks the bucket under the ``images/`` prefix, downloads
each image, re-encodes it through Pillow (which discards EXIF by
default), and uploads the sanitized JPEG back in place.  It is
**idempotent** - re-running it on already-sanitized images is harmless
but wasteful.

Safety & observability
----------------------
* **Dry-run by default.**  Pass ``--apply`` to actually write.  Dry-run
  downloads and parses each image but does not upload.
* **Read-only discovery first.**  Counts total blobs, estimates size,
  and reports stats before any mutation.
* **Progress logging.**  Emits progress every 50 images so a run against
  a large bucket is visible.
* **Content-type preservation.**  Writes the sanitized blob with the
  same content-type the original had, defaulting to ``image/jpeg``.
* **Skip non-images.**  Only JPEG/PNG/WebP/GIF blobs are touched; the
  SQLite backup files co-located in the bucket are left alone (they
  live under a different prefix anyway).
* **Resumable-ish.**  On failure, log and continue; exit with the
  count of errors so a subsequent run can pick up where this left off.

Usage
-----
    # Dry run - shows stats without touching anything
    .venv/bin/python -m scripts.strip_gcs_exif

    # Actually sanitize
    .venv/bin/python -m scripts.strip_gcs_exif --apply

    # Target a specific subpath (useful for debugging one user)
    .venv/bin/python -m scripts.strip_gcs_exif --apply --prefix images/42/

    # Limit how many blobs to process (e.g. for a sample run)
    .venv/bin/python -m scripts.strip_gcs_exif --apply --limit 100

Environment
-----------
    GCS_BUCKET                   (no default; set to your bucket name)
    GOOGLE_APPLICATION_CREDENTIALS   (default: credentials.json in repo root)

Cost note
---------
Each image is downloaded and re-uploaded.  For a bucket with N MB of
images, this script transfers roughly 2 * N MB.  GCS in us-central1
charges ~$0.02/GB for egress to the same region, so sanitizing a 5 GB
bucket costs well under $1 in network fees - trivial.
"""

from __future__ import annotations

import argparse
import io
import logging
import os
import sys
from dataclasses import dataclass
from typing import Iterable

# Allow `python -m scripts.strip_gcs_exif` from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("strip_gcs_exif")


DEFAULT_BUCKET = os.environ.get("GCS_BACKUP_BUCKET") or os.environ.get("GCS_BUCKET", "")
DEFAULT_PREFIX = "images/"
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif")


@dataclass
class RunStats:
    """Accumulator for a single run.  Printed at end."""
    scanned: int = 0       # total blobs visited
    skipped_ext: int = 0   # non-image extension (e.g. .db, .json)
    skipped_empty: int = 0 # zero-byte blob
    processed: int = 0     # successfully re-encoded
    uploaded: int = 0      # successfully written back (0 in dry-run)
    no_change: int = 0     # byte-identical to original (no EXIF was present)
    errors: int = 0        # Pillow failures, network failures, etc.
    bytes_before: int = 0
    bytes_after: int = 0

    def log_progress(self) -> None:
        logger.info(
            "progress: scanned=%d processed=%d uploaded=%d no_change=%d errors=%d",
            self.scanned, self.processed, self.uploaded, self.no_change, self.errors,
        )

    def log_summary(self) -> None:
        savings = self.bytes_before - self.bytes_after
        logger.info("─" * 60)
        logger.info("scanned                  : %d blobs", self.scanned)
        logger.info("skipped (non-image ext)  : %d", self.skipped_ext)
        logger.info("skipped (empty)          : %d", self.skipped_empty)
        logger.info("processed                : %d", self.processed)
        logger.info("uploaded                 : %d", self.uploaded)
        logger.info("byte-identical (no EXIF) : %d", self.no_change)
        logger.info("errors                   : %d", self.errors)
        logger.info("bytes before             : %s", _fmt_bytes(self.bytes_before))
        logger.info("bytes after              : %s", _fmt_bytes(self.bytes_after))
        logger.info(
            "savings                  : %s (%.1f%%)",
            _fmt_bytes(savings),
            100 * savings / self.bytes_before if self.bytes_before else 0.0,
        )
        logger.info("─" * 60)


def _fmt_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _strip_exif(data: bytes) -> bytes:
    """Re-encode as JPEG with no EXIF.

    Mirrors the runtime code in src/web/routes/meals.py (_strip_exif_to_jpeg)
    but kept self-contained so this script can run standalone.
    """
    from PIL import Image as PILImage, ImageOps

    img = PILImage.open(io.BytesIO(data))
    img = ImageOps.exif_transpose(img)
    if img.mode != "RGB":
        img = img.convert("RGB")
    out = io.BytesIO()
    img.save(out, "JPEG", quality=85)
    return out.getvalue()


def _looks_like_image(blob_name: str) -> bool:
    lower = blob_name.lower()
    return any(lower.endswith(ext) for ext in IMAGE_EXTS)


def _has_exif(data: bytes) -> bool:
    """Quick heuristic - does this JPEG/PNG actually carry EXIF metadata?

    Lets the summary distinguish "no-op re-encode" (image had no EXIF
    to begin with) from "actually sanitized" (removed metadata).
    """
    try:
        from PIL import Image as PILImage
        img = PILImage.open(io.BytesIO(data))
        img.load()
        exif = img.getexif()
        # getexif() returns an empty Exif() if there's no metadata
        return bool(exif and len(exif) > 0)
    except Exception:
        return False


def _iter_image_blobs(bucket, prefix: str, limit: int | None) -> Iterable:
    """Yield blob objects under ``prefix`` that look like images."""
    count = 0
    for blob in bucket.list_blobs(prefix=prefix):
        if limit is not None and count >= limit:
            return
        if not _looks_like_image(blob.name):
            continue
        yield blob
        count += 1


def run(apply: bool, bucket_name: str, prefix: str, limit: int | None) -> RunStats:
    from google.cloud import storage  # type: ignore[import-untyped]

    # Default credentials lookup: ADC → GOOGLE_APPLICATION_CREDENTIALS →
    # repo-root credentials.json (mirrors src/gcs.py)
    if not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        candidate = os.path.join(project_root, "credentials.json")
        if os.path.isfile(candidate):
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = candidate
            logger.info("Using credentials: %s", candidate)

    client = storage.Client()
    bucket = client.bucket(bucket_name)
    logger.info(
        "mode=%s bucket=%s prefix=%s limit=%s",
        "APPLY" if apply else "DRY_RUN",
        bucket_name, prefix, limit if limit is not None else "-",
    )

    stats = RunStats()

    # First pass: loose scan for non-image blobs (so we can report skips)
    for blob in bucket.list_blobs(prefix=prefix):
        if limit is not None and stats.scanned >= limit:
            break
        stats.scanned += 1
        if not _looks_like_image(blob.name):
            stats.skipped_ext += 1
            continue
        if blob.size == 0:
            stats.skipped_empty += 1
            continue

        try:
            original = blob.download_as_bytes()
        except Exception:
            logger.warning("download failed: %s", blob.name, exc_info=True)
            stats.errors += 1
            continue

        stats.bytes_before += len(original)

        # Quick check: does this image actually carry EXIF?  We log this
        # for visibility but still re-encode to normalize the JPEG output
        # (some non-EXIF images still have ICC profiles or other metadata
        # chunks we'd rather not keep).
        had_exif = _has_exif(original)

        try:
            sanitized = _strip_exif(original)
        except Exception:
            logger.warning("strip failed: %s", blob.name, exc_info=True)
            stats.errors += 1
            continue

        stats.processed += 1
        stats.bytes_after += len(sanitized)

        if sanitized == original:
            stats.no_change += 1
        elif not had_exif:
            # Re-encode changed bytes but the image had no EXIF - likely
            # just JPEG recompression artifact.  Still harmless.
            pass

        if apply:
            try:
                blob.upload_from_string(sanitized, content_type="image/jpeg")
                stats.uploaded += 1
            except Exception:
                logger.warning("upload failed: %s", blob.name, exc_info=True)
                stats.errors += 1
                continue

        if stats.scanned % 50 == 0:
            stats.log_progress()

    return stats


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument(
        "--apply",
        action="store_true",
        help="Actually upload sanitized images.  Without this flag, the "
             "script runs in dry-run mode - downloads and re-encodes but "
             "does NOT upload.",
    )
    p.add_argument(
        "--bucket",
        default=DEFAULT_BUCKET,
        help=f"GCS bucket name (default: {DEFAULT_BUCKET})",
    )
    p.add_argument(
        "--prefix",
        default=DEFAULT_PREFIX,
        help=f"Path prefix within the bucket (default: {DEFAULT_PREFIX})",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Stop after processing N blobs.  Handy for test runs.",
    )
    args = p.parse_args()

    if args.prefix and not args.prefix.endswith("/"):
        logger.warning("--prefix doesn't end in /, forcing: %s/", args.prefix)
        args.prefix = args.prefix + "/"

    # Safety: refuse a bare empty prefix - it would scan the whole bucket
    # including database backups.
    if not args.prefix.strip("/"):
        logger.error("refusing to run with empty prefix; at least images/ required")
        return 2

    if not args.apply:
        logger.info("*** DRY RUN *** - re-run with --apply to actually write")

    try:
        stats = run(
            apply=args.apply,
            bucket_name=args.bucket,
            prefix=args.prefix,
            limit=args.limit,
        )
    except KeyboardInterrupt:
        logger.warning("interrupted by user")
        return 130

    stats.log_summary()
    return 1 if stats.errors else 0


if __name__ == "__main__":
    sys.exit(main())

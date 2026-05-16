#!/usr/bin/env python3
"""Upload a file to Google Drive, maintaining a rolling max of N files in the folder.

Usage:
    python3 scripts/upload_gdrive.py <file_path> [--folder-id ID] [--max-files 5]

Requires:
    - credentials.json (service account) in project root or GOOGLE_SHEETS_CREDENTIALS env
    - The target Drive folder must be shared with the service account email
"""

import argparse
import logging
import os
import sys

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("gdrive_upload")

SCOPES = ["https://www.googleapis.com/auth/drive.file"]
DEFAULT_FOLDER_ID = "1AdfyebLn69HwjyCd8aRz5Ofo2FHlQIeI"


def get_drive_service():
    creds_path = os.environ.get(
        "GOOGLE_SHEETS_CREDENTIALS",
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "credentials.json"),
    )
    creds = service_account.Credentials.from_service_account_file(creds_path, scopes=SCOPES)
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def upload_file(service, file_path: str, folder_id: str) -> str:
    """Upload a file to a Drive folder. Returns the file ID."""
    name = os.path.basename(file_path)
    metadata = {"name": name, "parents": [folder_id]}
    media = MediaFileUpload(file_path, mimetype="application/gzip", resumable=True)
    f = service.files().create(body=metadata, media_body=media, fields="id,name,size").execute()
    logger.info("Uploaded %s (%s bytes) → Drive ID %s", f["name"], f.get("size", "?"), f["id"])
    return f["id"]


def cleanup_old(service, folder_id: str, max_files: int) -> None:
    """Delete oldest files beyond max_files in the folder (by name sort, newest first)."""
    results = service.files().list(
        q=f"'{folder_id}' in parents and trashed=false",
        fields="files(id,name,createdTime)",
        orderBy="name desc",
        pageSize=100,
    ).execute()
    files = results.get("files", [])
    if len(files) <= max_files:
        return
    for old in files[max_files:]:
        service.files().delete(fileId=old["id"]).execute()
        logger.info("Deleted old backup: %s", old["name"])


def main():
    parser = argparse.ArgumentParser(description="Upload backup to Google Drive")
    parser.add_argument("file_path", help="Path to file to upload")
    parser.add_argument("--folder-id", default=DEFAULT_FOLDER_ID)
    parser.add_argument("--max-files", type=int, default=5)
    args = parser.parse_args()

    if not os.path.isfile(args.file_path):
        logger.error("File not found: %s", args.file_path)
        sys.exit(1)

    service = get_drive_service()
    upload_file(service, args.file_path, args.folder_id)
    cleanup_old(service, args.folder_id, args.max_files)


if __name__ == "__main__":
    main()

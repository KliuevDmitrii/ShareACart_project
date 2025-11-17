# slack_uploader.py

import os
import json
import mimetypes
import requests


def upload_file_to_slack(filepath, slack_token, slack_channel, start_str, end_str):
    """Upload CSV file to Slack via getUploadURLExternal + completeUploadExternal."""

    if not slack_token or not slack_channel:
        print("⚠️ Slack not configured, skipping upload.")
        return

    filename_only = os.path.basename(filepath)
    file_size = os.path.getsize(filepath)

    # ============================================================
    # STEP 1 — request upload URL (must send JSON as bytes)
    # ============================================================
    body_json = {
        "filename": filename_only,
        "length": file_size
    }

    get_url_resp = requests.post(
        "https://slack.com/api/files.getUploadURLExternal",
        headers={
            "Authorization": f"Bearer {slack_token}",
            "Content-Type": "application/json;charset=utf-8"
        },
        data=json.dumps(body_json).encode("utf-8")  # CRITICAL FIX
    )

    get_url_json = get_url_resp.json()

    if not get_url_json.get("ok"):
        print("❌ getUploadURLExternal failed:", get_url_json)
        return False

    upload_url = get_url_json["upload_url"]
    file_id = get_url_json["file_id"]

    # ============================================================
    # STEP 2 — Upload CSV file to Slack’s S3 storage
    # ============================================================
    mime_type = mimetypes.guess_type(filepath)[0] or "text/csv"

    with open(filepath, "rb") as f:
        upload_resp = requests.put(
            upload_url,
            data=f,
            headers={"Content-Type": mime_type}
        )

    if upload_resp.status_code not in (200, 201):
        print("❌ Failed uploading file to Slack S3:", upload_resp.status_code, upload_resp.text)
        return False

    # ============================================================
    # STEP 3 — Finalize upload and publish in channel
    # ============================================================
    complete_body = {
        "files": [{"id": file_id, "title": filename_only}],
        "channels": [slack_channel],  # must be array
        "initial_comment": f"Sentry vendors report {start_str}–{end_str}"
    }

    complete_resp = requests.post(
        "https://slack.com/api/files.completeUploadExternal",
        headers={
            "Authorization": f"Bearer {slack_token}",
            "Content-Type": "application/json;charset=utf-8"
        },
        data=json.dumps(complete_body).encode("utf-8")
    )

    complete_json = complete_resp.json()

    if not complete_json.get("ok"):
        print("❌ completeUploadExternal failed:", complete_json)
        return False

    print("✅ File successfully uploaded to Slack!")
    return True

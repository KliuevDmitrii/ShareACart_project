# slack_uploader.py

import os
import requests

def upload_file_to_slack(filepath, slack_token, slack_channel, start_str, end_str):
    """Reliable upload using Slack files.uploadV2 (CI-safe)."""

    if not slack_token or not slack_channel:
        print("⚠️ Slack not configured. Skipping upload.")
        return False

    filename = os.path.basename(filepath)

    with open(filepath, "rb") as f:
        file_bytes = f.read()

    print("⏳ Uploading to Slack via files.uploadV2...")

    resp = requests.post(
        "https://slack.com/api/files.uploadV2",
        headers={
            "Authorization": f"Bearer {slack_token}"
        },
        data={
            "channel_id": slack_channel,
            "initial_comment": f"Sentry vendors report {start_str}–{end_str}",
            "filename": filename,
            "title": filename
        },
        files={"file": (filename, file_bytes, "text/csv")}
    )

    data = resp.json()

    if not data.get("ok"):
        print("❌ Slack upload failed:", data)
        return False

    print("✅ File uploaded to Slack successfully!")
    return True
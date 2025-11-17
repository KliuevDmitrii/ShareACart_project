import os
import requests
import csv
from datetime import datetime, timedelta
from dotenv import load_dotenv

# --- load .env (локально), в GitHub Actions переменные придут из secrets ---
load_dotenv()

ORG = os.getenv("SENTRY_ORG")
PROJECT = os.getenv("SENTRY_PROJECT")
TOKEN = os.getenv("SENTRY_TOKEN")
BASE_QUERY = os.getenv("SENTRY_QUERY")
SLACK_TOKEN = os.getenv("SLACK_TOKEN")
SLACK_CHANNEL = os.getenv("SLACK_CHANNEL")

if not ORG or not PROJECT or not TOKEN or not BASE_QUERY:
    raise ValueError("❌ Missing Sentry env vars (SENTRY_ORG/PROJECT/TOKEN/QUERY).")
if not SLACK_TOKEN or not SLACK_CHANNEL:
    print("⚠️ SLACK_TOKEN or SLACK_CHANNEL is not set. Report won't be sent to Slack.")

# --- исключаемые псевдо-вендоры ---
EXCLUDE = {e.lower() for e in [
    "unknown",
    "typeerror",
    "securityerror",
    "error",
    "syntaxerror",
    "NotAllowedError",
    "ReferenceError",
    "AbortError",
    "MonorailRequestError",
    "RuntimeError",
    "RpcError"
]}

# --- 7 days range ---
end_date = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
start_date = end_date - timedelta(days=7)

start_str = start_date.strftime("%Y-%m-%d")
end_str = (end_date - timedelta(seconds=1)).strftime("%Y-%m-%d")

# --- output folder ---
os.makedirs("report", exist_ok=True)
filename = f"report/sentry_report_{start_str}_to_{end_str}.csv"

headers = {"Authorization": f"Bearer {TOKEN}"}


def fetch_day(day_start):
    """Fetch issues for a 24h window using saved BASE_QUERY."""
    day_end = day_start + timedelta(days=1)

    query = (
        f"{BASE_QUERY} "
        f"lastSeen:>={day_start.strftime('%Y-%m-%d')} "
        f"lastSeen:<{day_end.strftime('%Y-%m-%d')}"
    )

    url = (
        f"https://sentry.io/api/0/projects/{ORG}/{PROJECT}/issues/"
        f"?query={query}"
    )

    r = requests.get(url, headers=headers)
    if r.status_code != 200:
        print("⚠️ Sentry API error:", r.status_code, r.text)
        return []

    return r.json()


def get_vendor(issue):
    """Extract vendor from metadata.type; skip error types."""
    meta = issue.get("metadata", {})
    v = meta.get("type", "")

    if not v:
        return None

    if v.lower() in EXCLUDE:
        return None

    return v


def upload_file_to_slack(filepath):
    """Send CSV file using Slack files.uploadV2 API (correct multipart body)."""

    if not SLACK_TOKEN or not SLACK_CHANNEL:
        print("⚠️ Slack not configured, skipping upload.")
        return

    url = "https://slack.com/api/files.uploadV2"

    with open(filepath, "rb") as f:
        multipart_form_data = {
            "file": (os.path.basename(filepath), f, "text/csv"),
            "channels": (None, SLACK_CHANNEL),
            "filename": (None, os.path.basename(filepath)),
            "title": (None, os.path.basename(filepath)),
            "initial_comment": (None, f"Sentry vendors report {start_str}–{end_str}")
        }

        response = requests.post(
            url,
            headers={"Authorization": f"Bearer {SLACK_TOKEN}"},
            files=multipart_form_data
        )

    data = response.json()

    if not data.get("ok"):
        print("❌ Slack upload failed:", data)
    else:
        print("✅ Report successfully sent to Slack via files.uploadV2.")




# --- aggregate ---
vendors = {}

print("⏳ Fetching Sentry data for last 7 days with saved query...")

for i in range(7):
    day = start_date + timedelta(days=i)
    print(f"  → {day.strftime('%Y-%m-%d')}")
    issues = fetch_day(day)

    for issue in issues:
        vendor = get_vendor(issue)
        if vendor is None:
            continue

        message = issue.get("title") or issue.get("metadata", {}).get("value") or ""
        events = int(issue.get("count", 0))  # count within this day window
        users = int(issue.get("userCount", 0))

        if vendor not in vendors:
            vendors[vendor] = {
                "Events": 0,
                "Users": 0,
                "Messages": []
            }

        vendors[vendor]["Events"] += events
        vendors[vendor]["Users"] += users
        vendors[vendor]["Messages"].append(message)

# --- sort & save CSV ---
sorted_vendors = sorted(vendors.items(), key=lambda x: x[1]["Events"], reverse=True)

with open(filename, "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["Rank", "Vendor", "Events", "Users", "Messages"])

    rank = 1
    for vendor, data in sorted_vendors:
        messages = "; ".join(sorted(set(data["Messages"])))
        w.writerow([rank, vendor, data["Events"], data["Users"], messages])
        rank += 1

print(f"✔ Report generated: {filename}")

# --- send to Slack ---
upload_file_to_slack(filename)

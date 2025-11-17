import os
import csv
import json
import requests
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

from slack_uploader import upload_file_to_slack


# === Load ENV ===
load_dotenv()

ORG = os.getenv("SENTRY_ORG")
PROJECT = os.getenv("SENTRY_PROJECT")
TOKEN = os.getenv("SENTRY_TOKEN")
BASE_QUERY = os.getenv("SENTRY_QUERY")

SLACK_TOKEN = os.getenv("SLACK_TOKEN")
SLACK_CHANNEL = os.getenv("SLACK_CHANNEL")

if not ORG or not PROJECT or not TOKEN or not BASE_QUERY:
    raise ValueError("❌ Missing Sentry environment variables")

# === Exclude list ===
EXCLUDE = {e.lower() for e in [
    "unknown", "typeerror", "securityerror", "error",
    "syntaxerror", "notallowederror", "referenceerror",
    "aborterror", "monorailrequesterror", "runtimeerror", "rpcerror"
]}

# === 7-day date range ===
now = datetime.now(timezone.utc)
end_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
start_date = end_date - timedelta(days=7)

start_str = start_date.strftime("%Y-%m-%d")
end_str = (end_date - timedelta(seconds=1)).strftime("%Y-%m-%d")

# === Output report path ===
os.makedirs("report", exist_ok=True)
filename = f"report/sentry_report_{start_str}_to_{end_str}.csv"

headers = {"Authorization": f"Bearer {TOKEN}"}


def fetch_day(day_start):
    day_end = day_start + timedelta(days=1)

    query = (
        f"{BASE_QUERY} "
        f"lastSeen:>={day_start.strftime('%Y-%m-%d')} "
        f"lastSeen:<{day_end.strftime('%Y-%m-%d')}"
    )

    url = f"https://sentry.io/api/0/projects/{ORG}/{PROJECT}/issues/?query={query}"
    r = requests.get(url, headers=headers)

    if r.status_code != 200:
        print("⚠️ Sentry error:", r.status_code, r.text)
        return []

    return r.json()


def get_vendor(issue):
    meta = issue.get("metadata", {})
    t = meta.get("type", "")
    if not t:
        return None
    if t.lower() in EXCLUDE:
        return None
    return t


# === Aggregation ===
vendors = {}

print("⏳ Fetching Sentry data for last 7 days...")

for i in range(7):
    day = start_date + timedelta(days=i)
    print(f"  → {day.strftime('%Y-%m-%d')}")
    issues = fetch_day(day)

    for issue in issues:
        vendor = get_vendor(issue)
        if vendor is None:
            continue

        message = issue.get("title") or issue.get("metadata", {}).get("value") or ""
        events = int(issue.get("count", 0))
        users = int(issue.get("userCount", 0))

        vendors.setdefault(vendor, {"Events": 0, "Users": 0, "Messages": []})
        vendors[vendor]["Events"] += events
        vendors[vendor]["Users"] += users
        vendors[vendor]["Messages"].append(message)

# === Save CSV ===
sorted_vendors = sorted(vendors.items(), key=lambda x: x[1]["Events"], reverse=True)

with open(filename, "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["Rank", "Vendor", "Events", "Users", "Messages"])

    for rank, (vendor, data) in enumerate(sorted_vendors, start=1):
        messages = "; ".join(sorted(set(data["Messages"])))
        w.writerow([rank, vendor, data["Events"], data["Users"], messages])

print(f"✔ Report generated: {filename}")

# === Send to Slack ===
upload_file_to_slack(filename, SLACK_TOKEN, SLACK_CHANNEL, start_str, end_str)
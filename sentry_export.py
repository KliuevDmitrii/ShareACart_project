import os
import csv
import requests
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()

ORG = os.getenv("SENTRY_ORG")
PROJECT = os.getenv("SENTRY_PROJECT")
TOKEN = os.getenv("SENTRY_TOKEN")
BASE_QUERY = os.getenv("SENTRY_QUERY")

headers = {"Authorization": f"Bearer {TOKEN}"}

#  EXCLUDE LIST — vendor types we ignore
EXCLUDE = {e.lower() for e in [
    "unknown", "typeerror", "securityerror", "error",
    "syntaxerror", "notallowederror", "referenceerror",
    "aborterror", "monorailrequesterror", "runtimeerror", "rpcerror"
]}

#  DATE RANGE (last 7 days)
now = datetime.now(timezone.utc)
end_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
start_date = end_date - timedelta(days=7)

start_str = start_date.strftime("%Y-%m-%d")
end_str = (end_date - timedelta(seconds=1)).strftime("%Y-%m-%d")

#  OUTPUT FOLDER
os.makedirs("report", exist_ok=True)
report_filename = f"report/sentry_report_{start_str}_to_{end_str}.csv"

#  Fetch last 2 releases from Sentry
def get_last_two_releases():
    url = f"https://sentry.io/api/0/projects/{ORG}/{PROJECT}/releases/?per_page=2"
    resp = requests.get(url, headers=headers)

    if resp.status_code != 200:
        print("❌ Failed to retrieve releases:", resp.text)
        return []

    data = resp.json()
    versions = [rel["version"] for rel in data[:2]]

    print(f"📦 Latest releases detected: {versions}")
    return versions


#  Fetch issues for a specific day (filtered by releases)
def fetch_day(day, release_filter):
    next_day = day + timedelta(days=1)

    query = (
        f"{BASE_QUERY} "
        f"{release_filter} "
        f"lastSeen:>={day:%Y-%m-%d} "
        f"lastSeen:<{next_day:%Y-%m-%d}"
    )

    url = f"https://sentry.io/api/0/projects/{ORG}/{PROJECT}/issues/?query={query}"
    resp = requests.get(url, headers=headers)

    if resp.status_code != 200:
        print("⚠️ Sentry API error:", resp.status_code, resp.text)
        return []

    return resp.json()


#  Extract vendor name
def get_vendor(issue):
    meta = issue.get("metadata", {})
    vendor = meta.get("type", "")

    if not vendor or vendor.lower() in EXCLUDE:
        return None

    return vendor


#  MAIN REPORT LOGIC
print("⏳ Fetching last 2 releases from Sentry...")
latest_releases = get_last_two_releases()

release_filter = ""
if latest_releases:
    release_filter = f'release:[{latest_releases[0]},{latest_releases[1]}] '
else:
    print("⚠️ No releases found. Report will run WITHOUT release filtering.")

vendors = {}

print("⏳ Fetching Sentry data for last 7 days...")
for i in range(7):
    day = start_date + timedelta(days=i)
    print(f"  → {day.strftime('%Y-%m-%d')}")

    issues = fetch_day(day, release_filter)

    for issue in issues:
        vendor = get_vendor(issue)
        if vendor is None:
            continue

        message = issue.get("title") or issue.get("metadata", {}).get("value", "")
        events = int(issue.get("count", 0))
        users = int(issue.get("userCount", 0))

        if vendor not in vendors:
            vendors[vendor] = {"Events": 0, "Users": 0, "Messages": []}

        vendors[vendor]["Events"] += events
        vendors[vendor]["Users"] += users
        vendors[vendor]["Messages"].append(message)


#  SAVE CSV REPORT
sorted_vendors = sorted(vendors.items(), key=lambda x: x[1]["Events"], reverse=True)

with open(report_filename, "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["Rank", "Vendor", "Events", "Users", "Messages"])

    for rank, (vendor, data) in enumerate(sorted_vendors, start=1):
        messages = "; ".join(sorted(set(data["Messages"])))
        w.writerow([rank, vendor, data["Events"], data["Users"], messages])

print(f"✔ Report generated: {report_filename}")

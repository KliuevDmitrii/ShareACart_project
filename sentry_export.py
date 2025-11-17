import os
import csv
import json
import requests
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()

ORG = os.getenv("SENTRY_ORG")
PROJECT = os.getenv("SENTRY_PROJECT")
TOKEN = os.getenv("SENTRY_TOKEN")
BASE_QUERY = os.getenv("SENTRY_QUERY")

EXCLUDE = {e.lower() for e in [
    "unknown", "typeerror", "securityerror", "error",
    "syntaxerror", "notallowederror", "referenceerror",
    "aborterror", "monorailrequesterror", "runtimeerror", "rpcerror"
]}

now = datetime.now(timezone.utc)
end_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
start_date = end_date - timedelta(days=7)

start_str = start_date.strftime("%Y-%m-%d")
end_str   = (end_date - timedelta(seconds=1)).strftime("%Y-%m-%d")

os.makedirs("report", exist_ok=True)
filename = f"report/sentry_report_{start_str}_to_{end_str}.csv"

headers = {"Authorization": f"Bearer {TOKEN}"}

def fetch_day(day):
    next_day = day + timedelta(days=1)
    query = f"{BASE_QUERY} lastSeen:>={day:%Y-%m-%d} lastSeen:<{next_day:%Y-%m-%d}"

    url = f"https://sentry.io/api/0/projects/{ORG}/{PROJECT}/issues/?query={query}"
    r = requests.get(url, headers=headers)
    return r.json() if r.status_code == 200 else []

def get_vendor(issue):
    meta = issue.get("metadata", {})
    t = meta.get("type", "")
    return None if not t or t.lower() in EXCLUDE else t

vendors = {}
print("⏳ Fetching Sentry data for last 7 days...")

for i in range(7):
    day = start_date + timedelta(days=i)
    print("  →", day.strftime("%Y-%m-%d"))
    issues = fetch_day(day)

    for issue in issues:
        vendor = get_vendor(issue)
        if vendor is None:
            continue

        message = issue.get("title") or issue.get("metadata", {}).get("value", "")
        events = int(issue.get("count", 0))
        users = int(issue.get("userCount", 0))

        vendors.setdefault(vendor, {"Events": 0, "Users": 0, "Messages": []})
        vendors[vendor]["Events"] += events
        vendors[vendor]["Users"] += users
        vendors[vendor]["Messages"].append(message)

sorted_vendors = sorted(vendors.items(), key=lambda x: x[1]["Events"], reverse=True)

with open(filename, "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["Rank", "Vendor", "Events", "Users", "Messages"])

    for rank, (vendor, data) in enumerate(sorted_vendors, start=1):
        messages = "; ".join(sorted(set(data["Messages"])))
        w.writerow([rank, vendor, data["Events"], data["Users"], messages])

print(f"✔ Report generated: {filename}")

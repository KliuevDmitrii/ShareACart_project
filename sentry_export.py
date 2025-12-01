import os
import csv
import requests
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()

# Configuration
ORG = os.getenv("SENTRY_ORG")
PROJECT = os.getenv("SENTRY_PROJECT")
TOKEN = os.getenv("SENTRY_TOKEN")
BASE_QUERY = os.getenv("SENTRY_QUERY")
REPORT_DAYS = int(os.getenv("REPORT_DAYS", "7"))
RELEASES_LIMIT = int(os.getenv("RELEASES_LIMIT", "2"))

headers = {"Authorization": f"Bearer {TOKEN}"}

# Exclude list — vendor types we ignore
EXCLUDE = {
    "unknown", "typeerror", "securityerror", "error",
    "syntaxerror", "notallowederror", "referenceerror",
    "aborterror", "monorailrequesterror", "runtimeerror", "rpcerror"
}

# Date range
now = datetime.now(timezone.utc)
end_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
start_date = end_date - timedelta(days=REPORT_DAYS)

start_str = start_date.strftime("%Y-%m-%d")
end_str = (end_date - timedelta(seconds=1)).strftime("%Y-%m-%d")

# Output folder
os.makedirs("report", exist_ok=True)
report_filename = f"report/sentry_report_{start_str}_to_{end_str}.csv"


def get_latest_releases(limit=RELEASES_LIMIT):
    """Fetch latest releases from Sentry."""
    url = f"https://sentry.io/api/0/projects/{ORG}/{PROJECT}/releases/?per_page={limit}"
    resp = requests.get(url, headers=headers)

    if resp.status_code != 200:
        print("❌ Failed to retrieve releases:", resp.text)
        return []

    data = resp.json()
    versions = [rel["version"] for rel in data[:limit]]
    return versions


def fetch_issues(start_date, end_date, releases=None):
    """Fetch all issues for the entire period with pagination."""
    import re

    # Build query with release filter integrated
    query_parts = [BASE_QUERY]

    if releases:
        query_parts.append(f'release:[{releases[0]},{releases[1]}]')

    query_parts.append(f"lastSeen:>={start_date:%Y-%m-%d}")
    query_parts.append(f"lastSeen:<={end_date:%Y-%m-%d}")

    query = " ".join(query_parts)

    # Calculate period for stats
    period_days = (end_date - start_date).days

    # Sentry API only supports: '', '24h', '14d'
    if period_days <= 1:
        stats_period = "24h"
    else:
        stats_period = "14d"

    all_issues = []
    cursor = None
    page = 1

    while True:
        url = f"https://sentry.io/api/0/projects/{ORG}/{PROJECT}/issues/?query={query}&statsPeriod={stats_period}"
        if cursor:
            url += f"&cursor={cursor}"

        resp = requests.get(url, headers=headers)

        if resp.status_code != 200:
            print(f"❌ API error: {resp.status_code}")
            print(f"   URL: {url}")
            try:
                error_data = resp.json()
                print(f"   Error: {error_data}")
            except:
                print(f"   Response: {resp.text[:200]}")
            break

        issues = resp.json()

        if len(issues) == 0:
            break

        all_issues.extend(issues)
        page += 1

        # Check for next page
        link_header = resp.headers.get('Link', '')
        if 'rel="next"' not in link_header:
            break

        # Extract cursor from Link header
        cursor_match = re.search(r'cursor=([^&>]+)', link_header)
        if cursor_match:
            cursor = cursor_match.group(1)
            page += 1
        else:
            break

    return all_issues


def get_vendor(issue):
    """Extract vendor name from issue metadata and normalize it."""
    vendor_raw = issue.get("metadata", {}).get("type", "")

    if not vendor_raw:
        return None

    vendor = vendor_raw.strip().lower()   # normalize

    if vendor in EXCLUDE:
        return None

    return vendor



def process_issues(issues):
    """Process issues and aggregate by vendor."""
    vendors = {}

    for issue in issues:
        vendor = get_vendor(issue)
        if vendor is None:
            continue

        message = issue.get("title") or issue.get("metadata", {}).get("value", "")

        # Get stats for the requested period from 'stats' field
        stats = issue.get("stats", {})

        # Try to get period-specific stats first, fallback to total count
        if stats:
            period_key = list(stats.keys())[0] if stats else None
            if period_key:
                stats_data = stats[period_key]
                events = sum(count for _, count in stats_data)
            else:
                events = int(issue.get("count", 0))
        else:
            events = int(issue.get("count", 0))

        if vendor not in vendors:
            vendors[vendor] = {
                "Events": 0,
                "Messages": [],
                "IssueCount": 0
            }

        vendors[vendor]["Events"] += events
        vendors[vendor]["Messages"].append(message)
        vendors[vendor]["IssueCount"] += 1

    return vendors


def save_report(vendors, filename, stats_period):
    """Save vendors data to CSV."""
    sorted_vendors = sorted(vendors.items(), key=lambda x: x[1]["Events"], reverse=True)

    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Rank", "Vendor", f"Events ({stats_period})", "Issues", "Messages"])

        for rank, (vendor, data) in enumerate(sorted_vendors, start=1):
            pretty_vendor = vendor.capitalize()
            messages = "; ".join(sorted(set(data["Messages"])))
            writer.writerow([
                rank,
                pretty_vendor,
                data["Events"],
                data["IssueCount"],
                messages
            ])

    return sorted_vendors



def main():
    """Main execution."""
    print(f"📅 Report Period: {start_str} to {end_str}\n")

    releases = get_latest_releases()
    if releases:
        print(f"📦 Releases: {releases}")

    issues = fetch_issues(start_date, end_date, releases)
    print(f"📊 Found {len(issues)} issues")

    vendors = process_issues(issues)

    period_days = (end_date - start_date).days
    stats_period_used = "24h" if period_days <= 1 else "14d"

    sorted_vendors = save_report(vendors, report_filename, stats_period_used)

    print(f"\n📋 TOP 10 VENDORS ({stats_period_used}):\n")
    for rank, (vendor, data) in enumerate(sorted_vendors[:10], start=1):
        print(f"   {rank:2}. {vendor:20} | Events: {data['Events']:>6,} | Issues: {data['IssueCount']:>3}")

    print(f"\n✅ Report saved: {report_filename}")


if __name__ == "__main__":
    main()
import os
import csv
import re
import requests
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()

# ===========================
# Configuration
# ===========================
ORG = os.getenv("SENTRY_ORG")
PROJECT = os.getenv("SENTRY_PROJECT")
TOKEN = os.getenv("SENTRY_TOKEN")
BASE_QUERY = (os.getenv("SENTRY_QUERY") or "").strip()

REPORT_DAYS = int(os.getenv("REPORT_DAYS", "7"))
RELEASES_LIMIT = int(os.getenv("RELEASES_LIMIT", "2"))

# Optional: filter releases by prefix (helps ignore unrelated versions like 0.6.24)
# Set empty to disable: RELEASE_PREFIX=
RELEASE_PREFIX = (os.getenv("RELEASE_PREFIX") or "3.").strip()

headers = {"Authorization": f"Bearer {TOKEN}"}

# Exclude list — vendor types we ignore (case-insensitive after normalization)
EXCLUDE = {
    "unknown", "typeerror", "securityerror", "error",
    "syntaxerror", "notallowederror", "referenceerror",
    "aborterror", "monorailrequesterror", "runtimeerror", "rpcerror"
}

# ===========================
# Date range (UTC)
# ===========================
now = datetime.now(timezone.utc)
end_date = now.replace(hour=0, minute=0, second=0, microsecond=0)  # today 00:00 UTC (exclusive)
start_date = end_date - timedelta(days=REPORT_DAYS)

# For filenames we show inclusive period: start .. yesterday
start_str = start_date.strftime("%Y-%m-%d")
end_str = (end_date - timedelta(days=1)).strftime("%Y-%m-%d")

# Output folder
os.makedirs("report", exist_ok=True)
report_filename = f"report/sentry_report_{start_str}_to_{end_str}.csv"


# ===========================
# Helpers
# ===========================
def parse_iso(dt_str: str) -> datetime | None:
    """Parse ISO datetime like 2026-01-12T03:26:26.824977Z to tz-aware datetime."""
    if not dt_str:
        return None
    try:
        return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
    except ValueError:
        return None


def get_latest_releases(limit: int, end_dt: datetime) -> list[str]:
    """
    Fetch latest releases from Sentry that existed before end_dt (dateCreated <= end_dt).
    This prevents selecting releases created AFTER the report period (which would yield 0 issues).

    Also supports optional RELEASE_PREFIX filtering (default "3.").
    """
    url = f"https://sentry.io/api/0/projects/{ORG}/{PROJECT}/releases/?per_page=100"
    resp = requests.get(url, headers=headers)

    if resp.status_code != 200:
        print("❌ Failed to retrieve releases:", resp.text)
        return []

    data = resp.json() or []
    if not isinstance(data, list):
        return []

    eligible = []
    for rel in data:
        version = (rel.get("version") or "").strip()
        created = parse_iso(rel.get("dateCreated"))

        if not version or not created:
            continue

        # IMPORTANT: only releases that existed before the report's end boundary
        if created > end_dt:
            continue

        # Optional prefix filter (helps exclude unrelated versions)
        if RELEASE_PREFIX and not version.startswith(RELEASE_PREFIX):
            continue

        eligible.append(rel)

    # Sort newest-first by dateCreated
    eligible.sort(key=lambda r: r.get("dateCreated") or "", reverse=True)

    # Pick unique versions
    versions: list[str] = []
    for rel in eligible:
        v = (rel.get("version") or "").strip()
        if v and v not in versions:
            versions.append(v)
        if len(versions) >= limit:
            break

    # Debug print (optional but helpful)
    print("🧾 Releases preview (first 10 eligible by dateCreated <= report end):")
    for rel in eligible[:10]:
        print(rel.get("version"), rel.get("dateCreated"))

    return versions


def build_query(start_dt: datetime, end_dt: datetime, releases: list[str] | None) -> tuple[str, str]:
    """
    Build Sentry search query + statsPeriod.
    statsPeriod supported: '', '24h', '14d' (per your earlier API errors).
    """
    query_parts = []
    if BASE_QUERY:
        query_parts.append(BASE_QUERY)

    # Release filter (safe for 0/1/2)
    if releases:
        if len(releases) == 1:
            query_parts.append(f"release:{releases[0]}")
        else:
            query_parts.append(f"release:[{releases[0]},{releases[1]}]")

    # lastSeen bounds for inclusive period: start..yesterday
    # end_dt is today 00:00, so yesterday = end_dt - 1 day
    query_parts.append(f"lastSeen:>={start_dt:%Y-%m-%d}")
    query_parts.append(f"lastSeen:<={(end_dt - timedelta(days=1)):%Y-%m-%d}")

    query = " ".join(query_parts)

    period_days = (end_dt - start_dt).days
    stats_period = "24h" if period_days <= 1 else "14d"

    return query, stats_period


def fetch_issues(start_dt: datetime, end_dt: datetime, releases: list[str] | None = None) -> tuple[list[dict], str]:
    """Fetch all issues for the entire period with pagination."""
    query, stats_period = build_query(start_dt, end_dt, releases)

    all_issues: list[dict] = []
    cursor = None

    while True:
        url = f"https://sentry.io/api/0/projects/{ORG}/{PROJECT}/issues/?query={query}&statsPeriod={stats_period}"
        if cursor:
            url += f"&cursor={cursor}"

        resp = requests.get(url, headers=headers)

        if resp.status_code != 200:
            print(f"❌ API error: {resp.status_code}")
            print(f"   URL: {url}")
            try:
                print(f"   Error: {resp.json()}")
            except Exception:
                print(f"   Response: {resp.text[:400]}")
            break

        issues = resp.json() or []
        if not issues:
            break

        all_issues.extend(issues)

        link_header = resp.headers.get("Link", "")
        if 'rel="next"' not in link_header:
            break

        m = re.search(r"cursor=([^&>]+)", link_header)
        if not m:
            break

        cursor = m.group(1)

    return all_issues, stats_period


def get_vendor(issue: dict) -> str | None:
    """Extract vendor name from issue metadata and normalize it (case-insensitive aggregation)."""
    vendor_raw = issue.get("metadata", {}).get("type", "")
    if not vendor_raw:
        return None

    vendor = vendor_raw.strip().lower()
    if not vendor or vendor in EXCLUDE:
        return None

    return vendor


def process_issues(issues: list[dict]) -> dict:
    """Process issues and aggregate by vendor."""
    vendors: dict[str, dict] = {}

    for issue in issues:
        vendor = get_vendor(issue)
        if vendor is None:
            continue

        message = issue.get("title") or issue.get("metadata", {}).get("value", "")

        stats = issue.get("stats") or {}
        events = 0

        # Try to sum period stats if available; fallback to total 'count'
        if stats:
            period_key = next(iter(stats.keys()), None)
            if period_key and isinstance(stats.get(period_key), list):
                events = sum(count for _, count in stats[period_key])
            else:
                events = int(issue.get("count", 0))
        else:
            events = int(issue.get("count", 0))

        if vendor not in vendors:
            vendors[vendor] = {"Events": 0, "Messages": [], "IssueCount": 0}

        vendors[vendor]["Events"] += events
        vendors[vendor]["Messages"].append(message)
        vendors[vendor]["IssueCount"] += 1

    return vendors


def save_report(vendors: dict, filename: str, stats_period: str) -> list:
    """Save vendors data to CSV."""
    sorted_vendors = sorted(vendors.items(), key=lambda x: x[1]["Events"], reverse=True)

    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Rank", "Vendor", f"Events ({stats_period})", "Issues", "Messages"])

        for rank, (vendor, data) in enumerate(sorted_vendors, start=1):
            pretty_vendor = vendor.capitalize()
            messages = "; ".join(sorted(set(data["Messages"])))
            writer.writerow([rank, pretty_vendor, data["Events"], data["IssueCount"], messages])

    return sorted_vendors


def main():
    if not ORG or not PROJECT or not TOKEN:
        raise SystemExit("Missing required env vars: SENTRY_ORG, SENTRY_PROJECT, SENTRY_TOKEN")

    print(f"📅 Report Period: {start_str} to {end_str}\n")

    releases = get_latest_releases(limit=RELEASES_LIMIT, end_dt=end_date)
    if releases:
        print(f"📦 Releases (<= report end): {releases}")
    else:
        print("⚠️ No eligible releases found for this report period. Running without release filter.")

    issues, stats_period_used = fetch_issues(start_date, end_date, releases if releases else None)
    print(f"📊 Found {len(issues)} issues\n")

    vendors = process_issues(issues)
    sorted_vendors = save_report(vendors, report_filename, stats_period_used)

    print(f"📋 TOP 10 VENDORS ({stats_period_used}):\n")
    for rank, (vendor, data) in enumerate(sorted_vendors[:10], start=1):
        print(f"   {rank:2}. {vendor:20} | Events: {data['Events']:>6,} | Issues: {data['IssueCount']:>3}")

    print(f"\n✅ Report saved: {report_filename}")


if __name__ == "__main__":
    main()
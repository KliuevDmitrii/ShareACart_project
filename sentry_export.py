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
RELEASES_LIMIT = int(os.getenv("RELEASES_LIMIT", "3"))

# Optional: restrict releases to a prefix (recommended)
RELEASE_PREFIX = (os.getenv("RELEASE_PREFIX") or "3.").strip()

headers = {"Authorization": f"Bearer {TOKEN}"}

EXCLUDE = {
    "unknown", "typeerror", "securityerror", "error",
    "syntaxerror", "notallowederror", "referenceerror",
    "aborterror", "monorailrequesterror", "runtimeerror", "rpcerror"
}

# ===========================
# Date range (UTC)
# ===========================
now = datetime.now(timezone.utc)
end_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
start_date = end_date - timedelta(days=REPORT_DAYS)

start_str = start_date.strftime("%Y-%m-%d")
end_str = (end_date - timedelta(days=1)).strftime("%Y-%m-%d")

os.makedirs("report", exist_ok=True)
report_filename = f"report/sentry_report_{start_str}_to_{end_str}.csv"

# ===========================
# Helpers
# ===========================
def parse_iso(dt: str):
    if not dt:
        return None
    try:
        return datetime.fromisoformat(dt.replace("Z", "+00:00"))
    except ValueError:
        return None


def semver_key(version: str):
    """
    Semantic-version-like comparison.
    3.1.3 > 3.1.2 > 3.1.1 > 3.0.39 > 3.0.6
    """
    v = version.split("+", 1)[0]
    has_prerelease = "-" in v
    base, _, prerelease = v.partition("-")

    nums = []
    for p in base.split("."):
        try:
            nums.append(int(p))
        except ValueError:
            nums.append(0)

    while len(nums) < 4:
        nums.append(0)

    final_flag = 0 if has_prerelease else 1
    prerelease_num = 0
    if prerelease:
        m = re.search(r"(\d+)", prerelease)
        if m:
            prerelease_num = int(m.group(1))

    return (*nums[:4], final_flag, prerelease_num)


def get_latest_releases(limit: int, end_dt: datetime) -> list[str]:
    url = f"https://sentry.io/api/0/projects/{ORG}/{PROJECT}/releases/?per_page=100"
    resp = requests.get(url, headers=headers)

    if resp.status_code != 200:
        print("❌ Failed to fetch releases:", resp.text)
        return []

    data = resp.json() or []
    versions = {}

    for rel in data:
        version = (rel.get("version") or "").strip()
        created = parse_iso(rel.get("dateCreated"))

        if not version or not created:
            continue
        if created > end_dt:
            continue
        if RELEASE_PREFIX and not version.startswith(RELEASE_PREFIX):
            continue

        versions[version] = rel.get("dateCreated")

    sorted_versions = sorted(versions.keys(), key=semver_key, reverse=True)

    print("🧾 Releases preview (top 10 by SEMVER):")
    for v in sorted_versions[:10]:
        print(v, versions[v])

    return sorted_versions[:limit]


def build_query(start_dt, end_dt, releases):
    parts = []
    if BASE_QUERY:
        parts.append(BASE_QUERY)

    if releases:
        if len(releases) == 1:
            parts.append(f"release:{releases[0]}")
        else:
            parts.append(f"release:[{','.join(releases)}]")

    parts.append(f"lastSeen:>={start_dt:%Y-%m-%d}")
    parts.append(f"lastSeen:<={(end_dt - timedelta(days=1)):%Y-%m-%d}")

    query = " ".join(parts)
    stats_period = "14d"
    return query, stats_period


def fetch_issues(start_dt, end_dt, releases):
    query, stats_period = build_query(start_dt, end_dt, releases)
    print(f"🔍 Using query: {query}")

    issues = []
    cursor = None

    while True:
        url = f"https://sentry.io/api/0/projects/{ORG}/{PROJECT}/issues/?query={query}&statsPeriod={stats_period}"
        if cursor:
            url += f"&cursor={cursor}"

        resp = requests.get(url, headers=headers)
        if resp.status_code != 200:
            print("❌ API error:", resp.text)
            break

        batch = resp.json()
        if not batch:
            break

        issues.extend(batch)

        link = resp.headers.get("Link", "")
        if 'rel="next"' not in link:
            break

        m = re.search(r"cursor=([^&>]+)", link)
        if not m:
            break
        cursor = m.group(1)

    return issues, stats_period


def get_vendor(issue):
    vendor = (issue.get("metadata", {}).get("type") or "").strip().lower()
    if not vendor or vendor in EXCLUDE:
        return None
    return vendor


def process_issues(issues):
    vendors = {}

    for issue in issues:
        vendor = get_vendor(issue)
        if not vendor:
            continue

        stats = issue.get("stats") or {}
        events = 0
        key = next(iter(stats), None)
        if key and isinstance(stats.get(key), list):
            events = sum(c for _, c in stats[key])
        else:
            events = int(issue.get("count", 0))

        vendors.setdefault(vendor, {"Events": 0, "Issues": 0, "Messages": set()})
        vendors[vendor]["Events"] += events
        vendors[vendor]["Issues"] += 1
        vendors[vendor]["Messages"].add(issue.get("title", ""))

    return vendors


def save_report(vendors, stats_period):
    sorted_items = sorted(vendors.items(), key=lambda x: x[1]["Events"], reverse=True)

    with open(report_filename, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Rank", "Vendor", f"Events ({stats_period})", "Issues", "Messages"])

        for i, (vendor, data) in enumerate(sorted_items, 1):
            w.writerow([
                i,
                vendor.capitalize(),
                data["Events"],
                data["Issues"],
                "; ".join(sorted(data["Messages"]))
            ])

    return sorted_items


def main():
    print(f"📅 Report Period: {start_str} to {end_str}\n")

    releases = get_latest_releases(RELEASES_LIMIT, end_date)
    print(f"📦 Releases (SEMVER): {releases}")

    issues, stats_period = fetch_issues(start_date, end_date, releases)
    print(f"📊 Found {len(issues)} issues\n")

    vendors = process_issues(issues)
    top = save_report(vendors, stats_period)

    print(f"📋 TOP 10 VENDORS ({stats_period}):\n")
    for i, (v, d) in enumerate(top[:10], 1):
        print(f"{i:2}. {v:20} | Events: {d['Events']:5} | Issues: {d['Issues']}")

    print(f"\n✅ Report saved: {report_filename}")


if __name__ == "__main__":
    main()
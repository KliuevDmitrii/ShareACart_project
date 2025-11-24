# 📊 Automated Sentry Weekly Report

This repository contains a fully automated reporting pipeline that collects error statistics from Sentry, generates a weekly CSV report, publishes it as a GitHub Release asset, and sends a Slack notification with a download button.

The automation runs entirely through **GitHub Actions** and does not require any direct integration between Sentry and Slack.

---

## 🚀 Features

- Fetches errors from Sentry for the last 7 days (rolling period)
- Automatically retrieves the **two latest releases** from Sentry
- Filters issues using a predefined Sentry search query
- Aggregates:
  - number of events
  - number of unique affected users
  - unique error messages
- Groups errors by vendor (Walmart, Amazon, Shopify, etc.)
- Generates a structured CSV report
- Automatically removes old GitHub Releases
- Uploads the new report as a Release asset
- Sends a Slack notification using **Block Kit** with a “Download CSV” button
- Fully automated weekly execution (every Monday)

---

## 📁 Project Structure

.
├── env/ # Local environment variables (ignored in git)
├── report/ # Generated CSV reports (via script)
├── requirements.txt # Python dependencies
└── sentry_export.py # Main script: fetches & generates Sentry report

## 🧠 How It Works

### 1. `sentry_export.py`
This script:

- loads configuration from environment variables
- determines the last 7-day period
- fetches the latest two Sentry releases via the Sentry API
- requests issues day-by-day using the combined query:
    BASE_QUERY + release:[x,y] + lastSeen:[start..end]
- groups errors by vendor
- aggregates statistics
- generates a CSV file named:
    sentry_report_YYYY-MM-DD_to_YYYY-MM-DD.csv

The generated report is saved into:
    /report/


---

## 🤖 GitHub Actions Automation

The workflow:

1. Runs every Monday at 11:00 Tbilisi time  
   (`cron: "0 7 * * MON"` in UTC)
2. Installs dependencies
3. Generates the Sentry report
4. Deletes all old releases
5. Creates a new GitHub Release
6. Uploads the CSV as a Release asset
7. Builds a public download URL
8. Sends a Slack message:

    📊 Weekly Sentry Report
    Your weekly Sentry error report is ready.
    [⬇️ Download CSV]


The Slack notification is delivered through a webhook and formatted with Block Kit.

---

## 🔧 Required Secrets

Set these in **GitHub → Settings → Secrets → Actions**:

| Secret | Description |
|--------|-------------|
| `SENTRY_ORG` | Sentry organization slug |
| `SENTRY_PROJECT` | Sentry project slug |
| `SENTRY_TOKEN` | Sentry API token (Bearer) |
| `SENTRY_QUERY` | Base Sentry search query (without release filter) |
| `SLACK_WEBHOOK_URL` | Incoming Slack webhook URL |

Example for `SENTRY_QUERY`:

    is:unresolved message:["result was null","check injected","result was STATUS_NOT_OK",vendor] browser.name:["Chrome Mobile",Chrome,Edge,Firefox]


---

## 📦 Installation (local development)

```bash
pip install -r requirements.txt
python sentry_export.py

#!/usr/bin/env python3
"""
Fetch program-name mapping data directly from NHLBI Jira API.

Replaces the manual step of exporting a CSV from the BDJW Jira project.
Queries the 'Program_name Mappings' saved filter (DATA AVAILABLE studies)
and writes a CSV in the same format expected by update_programs.py.

Usage:
    python fetch_jira_data.py --output /path/to/jira_mappings.csv

Environment variables (preferred for pod/CI use):
    JIRA_URL      Jira base URL  (default: https://nhlbijira.nhlbi.nih.gov)
    JIRA_TOKEN    Personal Access Token
    JIRA_USER     Username / email (optional, only needed for basic auth)

Or pass via CLI flags:
    python fetch_jira_data.py --jira-url URL --jira-token TOKEN --output out.csv
"""

import argparse
import csv
import os
import sys
import time
import requests


JIRA_URL_DEFAULT = "https://nhlbijira.nhlbi.nih.gov"

# JQL matching the saved "Program_name Mappings" filter
DEFAULT_JQL = (
    'project = "BDC-DMC Joint Workspace" '
    'AND issuetype = Dataset '
    'AND status = "DATA AVAILABLE"'
)

# Jira custom field IDs → CSV column names
FIELD_MAP = {
    "customfield_15206": "Accession",       # dbGaP accession
    "customfield_15909": "Program(s)",      # NHLBI program name (single select)
    "customfield_15209": "Gen3 Program Name",  # Gen3 program (text)
    "customfield_16100": "Community",       # BDC community (multi-select list)
    "status":            "Status",          # Issue status
    "summary":           "Summary",
}

# Fields to request from Jira (comma-separated for the API)
JIRA_FIELDS = ",".join([
    "customfield_15206",
    "customfield_15909",
    "customfield_15209",
    "customfield_16100",
    "status",
    "summary",
])

# CSV output columns — Community expands to up to 3 columns to match CSV export format
CSV_COLUMNS = [
    "Issue key",
    "Summary",
    "Status",
    "Accession",
    "Program(s)",
    "Gen3 Program Name",
    "Community",
    "Community.1",
    "Community.2",
]


def get_session(jira_url: str, token: str) -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    })
    # Validate connection
    resp = session.get(f"{jira_url}/rest/api/2/myself", timeout=30)
    resp.raise_for_status()
    user = resp.json()
    print(f"Connected to Jira as: {user.get('displayName', user.get('name'))} ({user.get('emailAddress', '')})")
    return session


def extract_field(fields: dict, field_id: str):
    """Extract a value from a Jira issue fields dict, handling different field types."""
    val = fields.get(field_id)
    if val is None:
        return ""

    # Single-select option (e.g. Program(s))
    if isinstance(val, dict) and "value" in val:
        return val["value"]

    # Multi-select list (e.g. Community)
    if isinstance(val, list):
        return [
            item["value"] if isinstance(item, dict) and "value" in item else str(item)
            for item in val
        ]

    # Status field (nested dict)
    if field_id == "status" and isinstance(val, dict):
        return val.get("name", "")

    return str(val).strip() if val else ""


def fetch_all_issues(session: requests.Session, jira_url: str, jql: str, page_size: int = 100):
    """Paginate through all Jira search results."""
    issues = []
    start_at = 0

    while True:
        params = {
            "jql": jql,
            "startAt": start_at,
            "maxResults": page_size,
            "fields": JIRA_FIELDS,
        }
        resp = session.get(f"{jira_url}/rest/api/2/search", params=params, timeout=60)
        resp.raise_for_status()
        data = resp.json()

        batch = data.get("issues", [])
        issues.extend(batch)

        total = data.get("total", 0)
        start_at += len(batch)

        print(f"  Fetched {start_at}/{total} issues...", end="\r")

        if start_at >= total or not batch:
            break

        time.sleep(0.2)  # Be polite to the Jira server

    print(f"  Fetched {len(issues)} issues total.        ")
    return issues


def issues_to_rows(issues: list) -> list:
    """Convert Jira issue dicts to CSV row dicts."""
    rows = []
    for issue in issues:
        key = issue.get("key", "")
        fields = issue.get("fields", {})

        accession = extract_field(fields, "customfield_15206")
        program = extract_field(fields, "customfield_15909")
        gen3_program = extract_field(fields, "customfield_15209")
        community_raw = extract_field(fields, "customfield_16100")
        status = extract_field(fields, "status")
        summary = extract_field(fields, "summary")

        # Community is a multi-value list — split into up to 3 columns
        if isinstance(community_raw, list):
            communities = community_raw
        elif community_raw:
            communities = [community_raw]
        else:
            communities = []

        rows.append({
            "Issue key":        key,
            "Summary":          summary,
            "Status":           status,
            "Accession":        accession,
            "Program(s)":       program,
            "Gen3 Program Name": gen3_program,
            "Community":        communities[0] if len(communities) > 0 else "",
            "Community.1":      communities[1] if len(communities) > 1 else "",
            "Community.2":      communities[2] if len(communities) > 2 else "",
        })

    return rows


def write_csv(rows: list, output_path: str):
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Saved {len(rows)} rows to: {output_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Fetch Jira program-name mappings and write to CSV",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--output", required=True,
                        help="Output CSV path")
    parser.add_argument("--jira-url",
                        default=os.environ.get("JIRA_URL", JIRA_URL_DEFAULT),
                        help="Jira base URL (env: JIRA_URL)")
    parser.add_argument("--jira-token",
                        default=os.environ.get("JIRA_TOKEN", ""),
                        help="Jira Personal Access Token (env: JIRA_TOKEN)")
    parser.add_argument("--jql",
                        default=DEFAULT_JQL,
                        help="JQL query override")
    parser.add_argument("--page-size", type=int, default=100,
                        help="Issues per API request (default: 100)")

    args = parser.parse_args()

    if not args.jira_token:
        print("Error: Jira token required. Use --jira-token or set JIRA_TOKEN env var.", file=sys.stderr)
        sys.exit(1)

    print(f"Jira URL: {args.jira_url}")
    print(f"JQL: {args.jql}")
    print()

    try:
        session = get_session(args.jira_url, args.jira_token)

        print("Fetching issues...")
        issues = fetch_all_issues(session, args.jira_url, args.jql, args.page_size)

        rows = issues_to_rows(issues)
        write_csv(rows, args.output)

        # Summary
        with_accession = sum(1 for r in rows if r["Accession"])
        without_accession = len(rows) - with_accession
        print(f"  With accession: {with_accession}")
        if without_accession:
            print(f"  Without accession: {without_accession} (will be skipped by update_programs.py)")

    except requests.exceptions.HTTPError as e:
        print(f"Jira API error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

"""
Job Alert Bot
-------------
Watches company job boards (Greenhouse, Lever, Ashby, Workday), keeps only
new US-based GTM / marketing-ops roles, and emails a digest.

Usage:
    python job_alert.py            # fetch, filter, email anything new
    python job_alert.py --dry-run  # print new matches, don't email or save
    python job_alert.py --check    # test that every board in companies.yaml responds
"""

from __future__ import annotations

import argparse
import json
import os
import re
import smtplib
import sys
from dataclasses import dataclass, asdict
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).parent
COMPANIES_FILE = ROOT / "companies.yaml"
SEEN_FILE = ROOT / "seen_jobs.json"
TIMEOUT = 20
HEADERS = {"User-Agent": "job-alert-bot (personal job search tool)"}

# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------

# A title must match at least one of these...
INCLUDE_KEYWORDS = [
    r"marketing op", r"marketing automation", r"marketing technolog",
    r"\bgtm\b", r"go[- ]to[- ]market", r"revenue op", r"\brevops\b",
    r"email marketing", r"lifecycle", r"\bcrm\b", r"growth marketing",
    r"\bgrowth\b", r"demand gen", r"campaign", r"hubspot", r"marketo",
    r"marketing analyst", r"marketing specialist", r"marketing coordinator",
    r"marketing associate", r"marketing manager",
]

# ...and none of these (too senior, or sales disguised as marketing).
EXCLUDE_KEYWORDS = [
    r"\bdirector\b", r"\bvp\b", r"vice president", r"\bhead of\b",
    r"\bprincipal\b", r"\bstaff\b", r"\bchief\b", r"senior manager",
    r"sr\.? manager", r"account executive", r"sales development",
    r"business development rep", r"\bsdr\b", r"\bbdr\b", r"\bintern\b",
    r"engineer", r"developer",
]

US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska",
    "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey",
    "NM": "New Mexico", "NY": "New York", "NC": "North Carolina",
    "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon",
    "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
    "DC": "District of Columbia",
}
US_CITIES = [
    "new york", "nyc", "san francisco", "austin", "houston", "dallas",
    "seattle", "boston", "chicago", "los angeles", "atlanta", "denver",
    "miami", "round rock", "detroit", "westlake", "salt lake",
]
NON_US_HINTS = [
    "canada", "toronto", "vancouver", "london", "united kingdom", "uk",
    "ireland", "dublin", "germany", "berlin", "india", "bangalore",
    "bengaluru", "hyderabad", "singapore", "australia", "sydney", "japan",
    "tokyo", "mexico", "brazil", "france", "paris", "netherlands",
    "amsterdam", "poland", "spain", "israel", "philippines", "emea", "apac",
    "latam",
]

INCLUDE_RE = re.compile("|".join(INCLUDE_KEYWORDS), re.I)
EXCLUDE_RE = re.compile("|".join(EXCLUDE_KEYWORDS), re.I)
STATE_ABBR_RE = re.compile(r"(?:,\s*|\b)(" + "|".join(US_STATES) + r")\b")
STATE_NAME_RE = re.compile("|".join(re.escape(s) for s in US_STATES.values()), re.I)
US_RE = re.compile(r"united states|\busa?\b|\bu\.s\.|" + "|".join(US_CITIES), re.I)
NON_US_RE = re.compile(r"\b(" + "|".join(NON_US_HINTS) + r")\b", re.I)


def title_matches(title: str) -> bool:
    return bool(INCLUDE_RE.search(title)) and not EXCLUDE_RE.search(title)


def is_us_location(location: str) -> bool:
    """True if the location looks US-based (or is too vague to rule out)."""
    loc = (location or "").strip()
    if not loc:
        return True  # unknown location: keep it rather than miss a job
    if US_RE.search(loc) or STATE_NAME_RE.search(loc) or STATE_ABBR_RE.search(loc):
        return True
    if NON_US_RE.search(loc):
        return False
    # Vague values like "Remote", "Hybrid" or Workday's "3 Locations"
    return bool(re.fullmatch(r"(remote|hybrid|anywhere|\d+ locations?)[\s\-–,()a-z]*", loc, re.I))


# ---------------------------------------------------------------------------
# Job model + ATS parsers (pure functions, so they're easy to test)
# ---------------------------------------------------------------------------

@dataclass
class Job:
    company: str
    title: str
    location: str
    url: str
    job_id: str

    @property
    def key(self) -> str:
        return f"{self.company}::{self.job_id}"


def parse_greenhouse(company: str, data: dict) -> list[Job]:
    return [
        Job(company, j.get("title", ""), (j.get("location") or {}).get("name", ""),
            j.get("absolute_url", ""), str(j.get("id")))
        for j in data.get("jobs", [])
    ]


def parse_lever(company: str, data: list) -> list[Job]:
    jobs = []
    for j in data:
        cats = j.get("categories") or {}
        locs = cats.get("allLocations") or [cats.get("location", "")]
        jobs.append(Job(company, j.get("text", ""), " / ".join(filter(None, locs)),
                        j.get("hostedUrl", ""), str(j.get("id"))))
    return jobs


def parse_ashby(company: str, data: dict) -> list[Job]:
    jobs = []
    for j in data.get("jobs", []):
        if j.get("isListed") is False:
            continue
        locs = [j.get("location", "")] + [s.get("location", "") for s in j.get("secondaryLocations") or []]
        country = (((j.get("address") or {}).get("postalAddress") or {}).get("addressCountry") or "")
        location = " / ".join(filter(None, locs + [country]))
        jobs.append(Job(company, j.get("title", ""), location, j.get("jobUrl", ""), str(j.get("id"))))
    return jobs


def parse_workday(company: str, host: str, site: str, data: dict) -> list[Job]:
    return [
        Job(company, p.get("title", ""), p.get("locationsText", ""),
            f"https://{host}/{site}{p.get('externalPath', '')}", p.get("externalPath", ""))
        for p in data.get("jobPostings", [])
    ]


# ---------------------------------------------------------------------------
# Fetchers
# ---------------------------------------------------------------------------

WORKDAY_SEARCHES = ["marketing", "growth", "go-to-market", "CRM", "revenue operations"]


def fetch_company(c: dict) -> list[Job]:
    ats, name = c["ats"], c["name"]
    if ats == "greenhouse":
        r = requests.get(f"https://boards-api.greenhouse.io/v1/boards/{c['slug']}/jobs",
                         headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        return parse_greenhouse(name, r.json())
    if ats == "lever":
        r = requests.get(f"https://api.lever.co/v0/postings/{c['slug']}?mode=json",
                         headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        return parse_lever(name, r.json())
    if ats == "ashby":
        r = requests.get(f"https://api.ashbyhq.com/posting-api/job-board/{c['slug']}",
                         headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        return parse_ashby(name, r.json())
    if ats == "workday":
        return fetch_workday(c)
    raise ValueError(f"Unknown ATS '{ats}' for {name}")


def fetch_workday(c: dict) -> list[Job]:
    """Workday has no official API; this calls the JSON endpoint its careers pages use."""
    host, site = c["host"], c["site"]
    tenant = host.split(".")[0]
    url = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
    jobs: dict[str, Job] = {}
    for term in WORKDAY_SEARCHES:
        total = None
        for offset in range(0, 100, 20):  # first 100 results per search term
            body = {"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": term}
            r = requests.post(url, json=body, headers=HEADERS | {"Accept": "application/json"},
                              timeout=TIMEOUT)
            r.raise_for_status()
            data = r.json()
            if total is None:  # Workday only reports the total on the first page
                total = data.get("total", 0)
            for job in parse_workday(c["name"], host, site, data):
                jobs[job.key] = job
            if offset + 20 >= total:
                break
    return list(jobs.values())


# ---------------------------------------------------------------------------
# State + email
# ---------------------------------------------------------------------------

def load_seen() -> set[str]:
    if SEEN_FILE.exists():
        return set(json.loads(SEEN_FILE.read_text()))
    return set()


def save_seen(seen: set[str]) -> None:
    SEEN_FILE.write_text(json.dumps(sorted(seen), indent=0))


def build_email(jobs: list[Job], failures: list[str]) -> tuple[str, str]:
    subject = f"🔔 {len(jobs)} new GTM / marketing ops role{'s' if len(jobs) != 1 else ''}"
    by_company: dict[str, list[Job]] = {}
    for j in jobs:
        by_company.setdefault(j.company, []).append(j)
    parts = ["<div style='font-family:Arial,sans-serif;font-size:14px'>"]
    for company in sorted(by_company):
        parts.append(f"<h3 style='margin:18px 0 6px'>{escape(company)}</h3><ul>")
        for j in by_company[company]:
            parts.append(f"<li><a href='{escape(j.url)}'>{escape(j.title)}</a>"
                         f" <span style='color:#666'>· {escape(j.location or 'Location not listed')}</span></li>")
        parts.append("</ul>")
    if failures:
        parts.append("<p style='color:#999;font-size:12px'>Boards that didn't respond: "
                     + escape(", ".join(failures)) + "</p>")
    parts.append("</div>")
    return subject, "".join(parts)


def send_email(subject: str, html: str) -> None:
    user = os.environ["GMAIL_ADDRESS"]
    password = os.environ["GMAIL_APP_PASSWORD"]
    to = os.environ.get("ALERT_TO", user)
    msg = MIMEMultipart("alternative")
    msg["Subject"], msg["From"], msg["To"] = subject, user, to
    msg.attach(MIMEText(html, "html"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(user, password)
        s.sendmail(user, [to], msg.as_string())


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def load_companies() -> list[dict]:
    return yaml.safe_load(COMPANIES_FILE.read_text())["companies"]


def run_check() -> int:
    ok = 0
    companies = load_companies()
    for c in companies:
        try:
            n = len(fetch_company(c))
            print(f"  ✅ {c['name']:<20} {n} open jobs")
            ok += 1
        except Exception as e:  # noqa: BLE001
            print(f"  ❌ {c['name']:<20} {type(e).__name__}: {e}")
    print(f"\n{ok}/{len(companies)} boards working")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="print matches; don't email or save")
    ap.add_argument("--check", action="store_true", help="test every board in companies.yaml")
    args = ap.parse_args()

    if args.check:
        return run_check()

    seen = load_seen()
    new_jobs: list[Job] = []
    failures: list[str] = []

    for c in load_companies():
        try:
            jobs = fetch_company(c)
        except Exception as e:  # one bad board shouldn't stop the rest
            print(f"⚠️  {c['name']}: {type(e).__name__}: {e}", file=sys.stderr)
            failures.append(c["name"])
            continue
        matches = [j for j in jobs if title_matches(j.title) and is_us_location(j.location)]
        print(f"{c['name']:<20} {len(jobs):>4} jobs  →  {len(matches)} match")
        new_jobs += [j for j in matches if j.key not in seen]

    print(f"\n{len(new_jobs)} new matching jobs")
    for j in new_jobs:
        print(f"  • {j.company}: {j.title} ({j.location})\n    {j.url}")

    if args.dry_run:
        return 0

    if new_jobs:
        send_email(*build_email(new_jobs, failures))
        print("📧 Email sent")
    seen |= {j.key for j in new_jobs}
    save_seen(seen)
    return 0


if __name__ == "__main__":
    sys.exit(main())

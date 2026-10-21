"""Tests run on sample data only, so they need no internet connection."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import job_alert as ja  # noqa: E402


@pytest.mark.parametrize("title, expected", [
    ("Marketing Operations Specialist", True),
    ("Marketing Ops Manager", True),
    ("GTM Operations Analyst", True),
    ("Go-To-Market Strategy Associate", True),
    ("Email Marketing Specialist", True),
    ("Lifecycle Marketing Manager", True),
    ("Revenue Operations Analyst", True),
    ("Marketo Administrator, Marketing Automation", True),
    ("Director, Marketing Operations", False),   # too senior
    ("VP of Growth", False),
    ("Head of Lifecycle", False),
    ("Senior Manager, Demand Generation", False),
    ("Growth Account Executive", False),         # sales disguised as marketing
    ("Sales Development Representative", False),
    ("Software Engineer, Growth", False),
    ("Marketing Intern", False),
    ("Product Designer", False),
])
def test_title_filter(title, expected):
    assert ja.title_matches(title) is expected


@pytest.mark.parametrize("location, expected", [
    ("Austin, TX", True),
    ("New York, NY", True),
    ("Remote - US", True),
    ("United States", True),
    ("Round Rock, Texas", True),
    ("San Francisco / New York", True),
    ("Remote", True),               # vague: keep rather than miss it
    ("3 Locations", True),          # Workday multi-location
    ("", True),
    ("London, UK", False),
    ("Toronto, Canada", False),
    ("Bengaluru, India", False),
    ("Remote - EMEA", False),
    ("Dublin, Ireland", False),
])
def test_us_filter(location, expected):
    assert ja.is_us_location(location) is expected


def load(name):
    return json.loads((Path(__file__).parent / "fixtures" / name).read_text())


def test_parse_greenhouse():
    jobs = ja.parse_greenhouse("HubSpot", load("greenhouse.json"))
    assert len(jobs) == 2
    assert jobs[0].title == "Marketing Operations Specialist"
    assert jobs[0].location == "Austin, TX"
    assert jobs[0].key == "HubSpot::111"


def test_parse_lever():
    jobs = ja.parse_lever("Plaid", load("lever.json"))
    assert jobs[0].title == "Lifecycle Marketing Manager"
    assert jobs[0].location == "New York, NY / San Francisco, CA"
    assert jobs[0].url.startswith("https://jobs.lever.co/")


def test_parse_ashby_skips_unlisted():
    jobs = ja.parse_ashby("Ramp", load("ashby.json"))
    assert [j.title for j in jobs] == ["GTM Operations Analyst"]
    assert "United States" in jobs[0].location


def test_parse_workday_builds_links():
    jobs = ja.parse_workday("Dell", "dell.wd1.myworkdayjobs.com", "External", load("workday.json"))
    assert jobs[0].url == "https://dell.wd1.myworkdayjobs.com/External/job/Round-Rock-Texas/Marketing-Operations-Analyst_R123"
    assert jobs[0].location == "Round Rock, Texas"


def test_end_to_end_filtering():
    """Everything parsed, then filtered: only US, right-level GTM roles survive."""
    jobs = (ja.parse_greenhouse("HubSpot", load("greenhouse.json"))
            + ja.parse_lever("Plaid", load("lever.json"))
            + ja.parse_ashby("Ramp", load("ashby.json"))
            + ja.parse_workday("Dell", "dell.wd1.myworkdayjobs.com", "External", load("workday.json")))
    kept = {j.title for j in jobs if ja.title_matches(j.title) and ja.is_us_location(j.location)}
    assert kept == {
        "Marketing Operations Specialist",
        "Lifecycle Marketing Manager",
        "GTM Operations Analyst",
        "Marketing Operations Analyst",
    }


def test_email_groups_by_company_and_escapes():
    jobs = [ja.Job("Dell", "Marketing <Ops> Analyst", "Austin, TX", "https://x.test/1", "1"),
            ja.Job("Brex", "CRM Specialist", "", "https://x.test/2", "2")]
    subject, html = ja.build_email(jobs, failures=["Notion"])
    assert subject.startswith("🔔 2 new")
    assert html.index("Brex") < html.index("Dell")      # alphabetical
    assert "&lt;Ops&gt;" in html                         # escaped
    assert "Location not listed" in html
    assert "Notion" in html                              # failure footer


def test_seen_jobs_are_not_resent(tmp_path, monkeypatch):
    monkeypatch.setattr(ja, "SEEN_FILE", tmp_path / "seen.json")
    assert ja.load_seen() == set()
    ja.save_seen({"Dell::1", "Brex::2"})
    assert ja.load_seen() == {"Dell::1", "Brex::2"}

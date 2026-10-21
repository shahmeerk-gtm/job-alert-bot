# Job Alert Bot 🔔

**I automated my own job search.** This bot watches the careers pages of companies I want to work for, finds new go-to-market and marketing operations roles in the US, and emails me within hours of a posting going live.

## The problem

Applications sent in the first few hours of a posting get far more attention than ones sent days later. But checking 20+ careers pages by hand every day is slow, easy to forget, and full of noise: senior roles I'm not a fit for, sales jobs labeled as marketing, and postings outside the US.

## How it works

```
companies.yaml ──► fetch every board ──► filter titles + locations ──► skip jobs already seen ──► email digest
                   (Greenhouse, Lever,     (GTM / marketing ops,          (seen_jobs.json)
                    Ashby, Workday)         US only, right level)
```

1. **Collect.** Pulls open roles from four applicant tracking systems. Greenhouse, Lever and Ashby have public APIs; Workday doesn't, so the bot calls the same JSON endpoint Workday careers pages use.
2. **Filter.** Keeps titles like *Marketing Operations*, *GTM*, *Lifecycle*, *Email Marketing*, *RevOps* and *CRM*. Drops titles that are too senior (Director, VP, Head of) or that are really sales (Account Executive, SDR/BDR). Keeps US locations only.
3. **Deduplicate.** Remembers every job it has already sent, so each email contains only new postings.
4. **Notify.** Emails a digest grouped by company, with direct links to apply.
5. **Run on a schedule.** GitHub Actions runs it every 3 hours during the day for free, so it works even when my laptop is off.

It's built to be resilient: if one company's board is down or changes, the bot logs it, notes it in the email, and keeps checking the rest.

## Tech

Python · REST APIs · GitHub Actions (scheduled CI) · Gmail SMTP · pytest

## Setup

1. **Fork or clone** this repo.
2. **Create a Gmail app password.** Turn on 2-Step Verification for your Google account, then create an app password at Google Account → Security → App passwords.
3. **Add repo secrets** under Settings → Secrets and variables → Actions:
   - `GMAIL_ADDRESS`: the Gmail address that sends the alerts
   - `GMAIL_APP_PASSWORD`: the app password from step 2
   - `ALERT_TO` *(optional)*: where to send alerts, if different
4. **Pick your companies** in `companies.yaml`.
5. **Test it.** Go to Actions → Job alert → Run workflow, choose `check` to confirm every board responds, then `run`.

### Run locally

```bash
pip install -r requirements.txt
python job_alert.py --check     # test every board
python job_alert.py --dry-run   # list matches without emailing
pytest                          # run the tests (no internet needed)
```

## Customizing

- **Companies:** add an entry to `companies.yaml`. The slug comes from the careers-page URL; instructions are at the top of the file.
- **Keywords:** edit `INCLUDE_KEYWORDS` and `EXCLUDE_KEYWORDS` at the top of `job_alert.py`.
- **Schedule:** edit the `cron` line in `.github/workflows/job-alert.yml`.

## What's next

- Score each job against my resume and rank the digest by fit
- Post alerts to Slack as well as email
- Log every match to a Google Sheet to track how fast I apply

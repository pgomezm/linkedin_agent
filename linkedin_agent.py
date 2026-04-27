import json
import sqlite3
import logging
import smtplib
import sys
import time
from datetime import datetime, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from linkedin_api import Linkedin
from apscheduler.schedulers.blocking import BlockingScheduler
from dotenv import load_dotenv
import os
import config

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DB_PATH = "jobs.db"

# ── DB ─────────────────────────────────────────────────────────────────────────

def init_db():
    con = sqlite3.connect(DB_PATH)
    con.execute("""
        CREATE TABLE IF NOT EXISTS seen_jobs (
            job_id TEXT PRIMARY KEY,
            title TEXT,
            company TEXT,
            location TEXT,
            url TEXT,
            found_at TEXT
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS run_meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    con.commit()
    con.close()

def is_new(job_id: str) -> bool:
    con = sqlite3.connect(DB_PATH)
    row = con.execute("SELECT 1 FROM seen_jobs WHERE job_id=?", (job_id,)).fetchone()
    con.close()
    return row is None

def mark_seen(job: dict):
    con = sqlite3.connect(DB_PATH)
    con.execute(
        "INSERT OR IGNORE INTO seen_jobs VALUES (?,?,?,?,?,?)",
        (job["id"], job["title"], job["company"], job["location"],
         job["url"], datetime.now(timezone.utc).isoformat())
    )
    con.commit()
    con.close()

def is_first_run() -> bool:
    con = sqlite3.connect(DB_PATH)
    row = con.execute("SELECT value FROM run_meta WHERE key='first_run_done'").fetchone()
    con.close()
    return row is None

def mark_first_run_done():
    con = sqlite3.connect(DB_PATH)
    con.execute(
        "INSERT OR REPLACE INTO run_meta VALUES ('first_run_done', ?)",
        (datetime.now(timezone.utc).isoformat(),)
    )
    con.commit()
    con.close()

def get_seen_count() -> int:
    con = sqlite3.connect(DB_PATH)
    row = con.execute("SELECT COUNT(*) FROM seen_jobs").fetchone()
    con.close()
    return row[0] if row else 0

# ── SESSION CACHE ───────────────────────────────────────────────────────────────

_api_instance = None

def get_api():
    global _api_instance
    if _api_instance is None:
        log.info("Authenticating with LinkedIn (one-time)...")
        _api_instance = Linkedin(
            os.getenv("LINKEDIN_EMAIL"),
            os.getenv("LINKEDIN_PASSWORD"),
            cookies_dir="./",
        )
        log.info("Authentication successful — session cached.")
    return _api_instance

def reset_api():
    global _api_instance
    _api_instance = None

# ── FILTROS ─────────────────────────────────────────────────────────────────────

def passes_filter(title: str) -> bool:
    t = title.lower()
    cfg = config.SEARCH_CONFIG["filters"]
    domain = cfg.get("title_domain", [])
    role = cfg.get("title_role", [])
    if domain and not any(k.lower() in t for k in domain):
        return False
    if role and not any(k.lower() in t for k in role):
        return False
    exclude = cfg.get("title_exclude", [])
    if any(k.lower() in t for k in exclude):
        return False
    return True

def passes_description_filter(api, job_id: str) -> bool:
    cfg = config.SEARCH_CONFIG["filters"]
    must_keywords = cfg.get("description_keywords", [])
    exclude_keywords = cfg.get("description_exclude", [])
    if not must_keywords and not exclude_keywords:
        return True
    try:
        data = api.get_job(job_id)
        desc = data.get("description", {}).get("text", "") or ""
        desc_lower = desc.lower()
        if must_keywords and not any(k.lower() in desc_lower for k in must_keywords):
            return False
        if any(k.lower() in desc_lower for k in exclude_keywords):
            return False
        return True
    except Exception as e:
        log.warning("Could not fetch description for job %s: %s", job_id, e)
        return True

# ── BUSQUEDA ──────────────────────────────────────────────────────────────────

def fetch_jobs_for_location(api, location: str, limit: int, keywords: str = None) -> list:
    cfg = config.SEARCH_CONFIG
    kw = keywords or cfg["keywords"]
    log.info("  Searching: '%s' in '%s' (limit=%d)", kw, location, limit)

    raw = api.search_jobs(
        keywords=kw,
        location_name=location,
        remote=cfg.get("remote", False),
        experience=cfg.get("experience"),
        job_type=cfg.get("job_type"),
        limit=limit,
    )

    jobs = []
    for r in raw:
        urn = r.get("trackingUrn", "") or r.get("jobPostingUrn", "")
        job_id = urn.split(":")[-1]
        title = r.get("title", "")

        # company — prueba múltiples paths
        company = "N/A"
        cd = r.get("companyDetails", {})
        for key in cd:
            name = cd[key].get("companyResolutionResult", {}).get("name")
            if name:
                company = name
                break

        location_str = r.get("formattedLocation", "") or r.get("jobPostingLocation", {}).get("displayName", "N/A")
        url = f"https://www.linkedin.com/jobs/view/{job_id}/"

        if not passes_filter(title):
            log.debug("  Skipped (title): %s", title)
            continue

        if not passes_description_filter(api, job_id):
            log.debug("  Skipped (description): %s", title)
            continue

        jobs.append({"id": job_id, "title": title, "company": company,
                     "location": location_str, "url": url})

    log.info("  -> %d jobs after filters", len(jobs))
    return jobs

def fetch_jobs(api, limit: int) -> list:
    cfg = config.SEARCH_CONFIG
    locations = cfg.get("locations", [cfg.get("location_name", "Worldwide")])
    keywords_list = cfg.get("keywords_list") or [cfg["keywords"]]
    seen_ids, all_jobs = set(), []

    for kw in keywords_list:
        for loc in locations:
            try:
                for j in fetch_jobs_for_location(api, loc, limit, keywords=kw):
                    if j["id"] not in seen_ids:
                        seen_ids.add(j["id"])
                        all_jobs.append(j)
            except Exception as e:
                log.warning("  Search failed ('%s' in '%s'): %s — skipping", kw, loc, e)
            time.sleep(3)

    log.info("Total unique jobs across all locations: %d", len(all_jobs))
    return all_jobs

# ── NOTIFICACIONES ────────────────────────────────────────────────────────────
# Gmail SMTP con App Password.
# En .env:
#   NOTIFY_EMAIL_FROM=pgomezm@gmail.com
#   NOTIFY_EMAIL_TO=pgomezm@gmail.com
#   GMAIL_APP_PASSWORD=abcdefghijklmnop  (16 chars, sin espacios)

def notify_email(new_jobs: list):
    sender   = os.getenv("NOTIFY_EMAIL_FROM")
    receiver = os.getenv("NOTIFY_EMAIL_TO")
    password = os.getenv("GMAIL_APP_PASSWORD")

    if not sender or not password:
        log.warning("Email not configured, skipping notification.")
        return

    subject = f"🔔 {len(new_jobs)} new LinkedIn jobs found"
    body = ""
    for j in new_jobs:
        body += f"{j['title']} — {j['company']}\n"
        body += f"📍 {j['location']}\n"
        body += f"🔗 {j['url']}\n"
        body += "-" * 50 + "\n"

    msg = MIMEMultipart()
    msg["From"]    = sender
    msg["To"]      = receiver
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(sender, password)
            server.sendmail(sender, receiver, msg.as_string())
        log.info("Email sent to %s", receiver)
    except Exception as e:
        log.error("Failed to send email: %s", e)

# ── CICLO PRINCIPAL ───────────────────────────────────────────────────────────

def run():
    log.info("=== Starting search cycle ===")
    try:
        first = is_first_run()
        limit = (config.SEARCH_CONFIG.get("limit_first_run", 100) if first
                 else config.SEARCH_CONFIG.get("limit_incremental", 20))
        log.info("%s run — limit=%d", "First" if first else "Incremental", limit)

        try:
            api = get_api()
        except Exception as auth_err:
            log.warning("Session error (%s) — resetting and retrying...", auth_err)
            reset_api()
            api = get_api()

        jobs     = fetch_jobs(api, limit)
        new_jobs = [j for j in jobs if is_new(j["id"])]
        log.info("New jobs: %d  |  Already seen: %d  |  DB total: %d",
                 len(new_jobs), len(jobs) - len(new_jobs), get_seen_count())

        for j in new_jobs:
            mark_seen(j)

        if first:
            mark_first_run_done()
            log.info("First run complete — %d jobs indexed, no email sent.", len(new_jobs))
        else:
            if new_jobs:
                notify_email(new_jobs)
                with open("new_jobs_latest.json", "w") as f:
                    json.dump(new_jobs, f, indent=2, ensure_ascii=False)
                log.info("Results saved to new_jobs_latest.json")
            else:
                log.info("No new jobs found.")

    except Exception as e:
        log.error("Cycle error: %s", e, exc_info=True)

# ── REPORT ────────────────────────────────────────────────────────────────────

def get_all_jobs() -> list:
    con = sqlite3.connect(DB_PATH)
    rows = con.execute("SELECT job_id, title, company, location, url, found_at FROM seen_jobs ORDER BY found_at DESC").fetchall()
    con.close()
    return [{"id": r[0], "title": r[1], "company": r[2], "location": r[3], "url": r[4], "found_at": r[5]} for r in rows]

def reset_db():
    con = sqlite3.connect(DB_PATH)
    con.execute("DELETE FROM seen_jobs")
    con.execute("DELETE FROM run_meta")
    con.commit()
    con.close()
    log.info("Database cleared.")

def list_jobs():
    jobs = get_all_jobs()
    if not jobs:
        print("No hay jobs en la DB.")
        return
    print(f"\n{'='*60}")
    print(f"  {len(jobs)} jobs en la DB")
    print(f"{'='*60}")
    for j in jobs:
        print(f"\n{j['title']} — {j['company']}")
        print(f"  📍 {j['location']}")
        print(f"  🔗 {j['url']}")
        print(f"  📅 {j['found_at']}")
        print(f"  {'-'*50}")

def send_db_report():
    jobs = get_all_jobs()
    if not jobs:
        log.info("No jobs in DB to report.")
        return
    sender   = os.getenv("NOTIFY_EMAIL_FROM")
    receiver = os.getenv("NOTIFY_EMAIL_TO")
    password = os.getenv("GMAIL_APP_PASSWORD")
    if not sender or not password:
        log.error("Email not configured. Verify NOTIFY_EMAIL_FROM and GMAIL_APP_PASSWORD in .env")
        return
    subject = f"📋 LinkedIn DB Report — {len(jobs)} jobs registrados"
    body = f"Total: {len(jobs)} jobs en la base de datos\n"
    body += "=" * 60 + "\n\n"
    for j in jobs:
        body += f"{j['title']} — {j['company']}\n"
        body += f"  📍 {j['location']}\n"
        body += f"  🔗 {j['url']}\n"
        body += f"  📅 {j['found_at']}\n"
        body += "-" * 50 + "\n"
    msg = MIMEMultipart()
    msg["From"]    = sender
    msg["To"]      = receiver
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(sender, password)
            server.sendmail(sender, receiver, msg.as_string())
        log.info("Report sent to %s (%d jobs)", receiver, len(jobs))
    except Exception as e:
        log.error("Failed to send report: %s", e)

# ── SCHEDULER ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    init_db()

    if "--send-report" in sys.argv:
        send_db_report()
        sys.exit(0)

    if "--reset-db" in sys.argv:
        reset_db()
        sys.exit(0)

    if "--list-jobs" in sys.argv:
        list_jobs()
        sys.exit(0)

    run()

    scheduler = BlockingScheduler()
    scheduler.add_job(run, "interval", hours=config.SCHEDULE_HOURS)
    log.info("Scheduler running — next execution in %dh. Press Ctrl+C to stop.", config.SCHEDULE_HOURS)
    try:
        scheduler.start()
    except KeyboardInterrupt:
        log.info("Stopped by user.")
        scheduler.shutdown()
        sys.exit(0)

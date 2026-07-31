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

# Absolute path so the DB is always next to this script, regardless of CWD
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jobs.db")

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
            found_at TEXT,
            score INTEGER DEFAULT 0,
            status TEXT DEFAULT 'new'
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS run_meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    # Migrate existing DB — add columns if missing
    for col, definition in [("score", "INTEGER DEFAULT 0"), ("status", "TEXT DEFAULT 'new'")]:
        try:
            con.execute(f"ALTER TABLE seen_jobs ADD COLUMN {col} {definition}")
        except sqlite3.OperationalError:
            pass
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
        "INSERT OR IGNORE INTO seen_jobs VALUES (?,?,?,?,?,?,?,?)",
        (job["id"], job["title"], job["company"], job["location"],
         job["url"], datetime.now(timezone.utc).isoformat(),
         job.get("score", 0), "new")
    )
    con.commit()
    con.close()

def set_job_status(job_id: str, status: str):
    valid = {"new", "applied", "interviewing", "rejected", "offer"}
    if status not in valid:
        log.error("Invalid status '%s'. Valid: %s", status, valid)
        return
    con = sqlite3.connect(DB_PATH)
    con.execute("UPDATE seen_jobs SET status=? WHERE job_id=?", (status, job_id))
    con.commit()
    con.close()
    log.info("Job %s → status: %s", job_id, status)

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

# ── SCORING ───────────────────────────────────────────────────────────────────

def compute_score(title: str, desc: str) -> int:
    score = 0
    t = title.lower()
    d = desc.lower()

    # Seniority in title
    if any(w in t for w in ["senior", "sr.", "sr "]):
        score += 20
    if any(w in t for w in ["lead", "tech lead", "technical lead"]):
        score += 15
    if any(w in t for w in ["manager", "engineering manager"]):
        score += 10

    # Title matches a keyword from our list
    keywords_list = config.SEARCH_CONFIG.get("keywords_list", [])
    if any(kw.lower() in t for kw in keywords_list):
        score += 10

    # Description keyword density (max 30 pts)
    desc_keywords = config.SEARCH_CONFIG["filters"].get("description_keywords", [])
    matched = sum(1 for k in desc_keywords if k.lower() in d)
    score += min(matched * 3, 30)

    # Remote bonus
    if "remote" in t or "remoto" in t or "remote" in d[:300]:
        score += 5

    return min(score, 100)

# ── AI SUMMARY ────────────────────────────────────────────────────────────────

def get_ai_summary(desc: str) -> str:
    if not desc or len(desc) < 100:
        return ""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return ""
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=200,
            messages=[{
                "role": "user",
                "content": (
                    "Resume este trabajo en exactamente 3 bullets muy cortos en español. "
                    "Incluye: tecnologías clave, tipo de empresa/proyecto, modalidad (remoto/presencial). "
                    "Formato exacto: • punto1 • punto2 • punto3\n\n"
                    f"Descripción:\n{desc[:2000]}"
                )
            }]
        )
        return next((b.text for b in response.content if b.type == "text"), "").strip()
    except Exception as e:
        log.warning("AI summary failed: %s", e)
        return ""

# ── FILTROS ───────────────────────────────────────────────────────────────────

def passes_filter(title: str) -> bool:
    t = title.lower()
    cfg = config.SEARCH_CONFIG["filters"]
    domain = cfg.get("title_domain", [])
    role = cfg.get("title_role", [])
    exclude = cfg.get("title_exclude", [])
    # Must match at least one domain keyword AND at least one role keyword,
    # UNLESS the title is already in our keywords_list (exact phrase match).
    keywords_list = config.SEARCH_CONFIG.get("keywords_list", [])
    if any(kw.lower() in t for kw in keywords_list):
        # Known exact keyword hit — skip domain/role check, only check exclude
        pass
    else:
        if domain and not any(k.lower() in t for k in domain):
            return False
        if role and not any(k.lower() in t for k in role):
            return False
    if any(k.lower() in t for k in exclude):
        return False
    return True

def get_job_description(api, job_id: str) -> str:
    try:
        data = api.get_job(job_id)
        return data.get("description", {}).get("text", "") or ""
    except Exception as e:
        log.warning("Could not fetch description for job %s: %s", job_id, e)
        return ""

def passes_description_filter(desc: str) -> bool:
    cfg = config.SEARCH_CONFIG["filters"]
    must_keywords = cfg.get("description_keywords", [])
    exclude_keywords = cfg.get("description_exclude", [])
    if not must_keywords and not exclude_keywords:
        return True
    desc_lower = desc.lower()
    if must_keywords and not any(k.lower() in desc_lower for k in must_keywords):
        return False
    if any(k.lower() in desc_lower for k in exclude_keywords):
        return False
    return True

# ── BUSQUEDA ──────────────────────────────────────────────────────────────────

def fetch_jobs_for_location(api, location, limit: int, keywords: str = None) -> list:
    cfg = config.SEARCH_CONFIG
    kw = keywords or cfg["keywords"]
    loc_label = location or "worldwide"
    log.info("  Searching: '%s' in '%s' (limit=%d)", kw, loc_label, limit)

    search_kwargs = dict(
        keywords=kw,
        listed_at=7 * 24 * 60 * 60,
        limit=limit,
    )
    if location:
        search_kwargs["location_name"] = location

    raw = api.search_jobs(**search_kwargs)

    log.info("  LinkedIn returned %d raw results", len(raw))
    jobs = []
    for r in raw:
        urn = r.get("trackingUrn", "") or r.get("jobPostingUrn", "")
        job_id = urn.split(":")[-1]
        if not job_id:
            continue
        title = r.get("title", "")

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
            log.info("  [SKIP title]  %s", title)
            continue

        desc = get_job_description(api, job_id)
        if not passes_description_filter(desc):
            log.info("  [SKIP desc]   %s", title)
            continue

        score = compute_score(title, desc)
        jobs.append({
            "id": job_id, "title": title, "company": company,
            "location": location_str, "url": url,
            "score": score, "_desc": desc[:2000],
        })

    log.info("  -> %d jobs after filters", len(jobs))
    return jobs

# The unofficial LinkedIn API returns account-based recommendations rather than
# true keyword-filtered results.  Running 3 broad queries without location gives
# ~150 diverse results to filter, far better than 21 near-identical location queries.
_SEARCH_QUERIES = [
    "firmware engineer embedded systems",
    "embedded software engineer microcontroller",
    "ingeniero firmware embebido sistemas",
]

def fetch_jobs(api, limit: int) -> list:
    seen_ids, all_jobs = set(), []

    for kw in _SEARCH_QUERIES:
        try:
            for j in fetch_jobs_for_location(api, location=None, limit=limit, keywords=kw):
                if j["id"] not in seen_ids:
                    seen_ids.add(j["id"])
                    all_jobs.append(j)
        except Exception as e:
            log.warning("  Search failed ('%s'): %s — skipping", kw, e)
        time.sleep(3)

    log.info("Total unique jobs: %d", len(all_jobs))
    return all_jobs

# ── NOTIFICACIONES ────────────────────────────────────────────────────────────

def _score_badge(score: int) -> str:
    if score >= 70:
        color = "#27ae60"
    elif score >= 50:
        color = "#f39c12"
    else:
        color = "#95a5a6"
    return (
        f'<span style="background:{color};color:white;padding:3px 9px;'
        f'border-radius:12px;font-weight:bold;font-size:13px">{score}</span>'
    )

def _build_html_email(jobs: list) -> str:
    rows = ""
    for j in sorted(jobs, key=lambda x: x.get("score", 0), reverse=True):
        summary_html = j.get("summary", "").replace("•", "<br>•") if j.get("summary") else "<em>—</em>"
        rows += f"""
        <tr>
          <td style="text-align:center">{_score_badge(j.get('score', 0))}</td>
          <td><strong>{j['title']}</strong><br><span style="color:#555;font-size:13px">{j['company']}</span></td>
          <td style="font-size:13px">{j['location']}</td>
          <td style="font-size:13px;color:#444">{summary_html}</td>
          <td><a href="{j['url']}" style="background:#0077b5;color:white;padding:6px 14px;border-radius:4px;text-decoration:none;font-size:13px">Ver</a></td>
        </tr>"""

    date_str = datetime.now().strftime("%d/%m/%Y %H:%M")
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  body {{ font-family: Arial, sans-serif; max-width: 960px; margin: 0 auto; padding: 20px; color: #333; }}
  h2 {{ color: #0077b5; margin-bottom: 4px; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 16px; }}
  th {{ background: #0077b5; color: white; padding: 10px 12px; text-align: left; font-size: 14px; }}
  td {{ padding: 10px 12px; border-bottom: 1px solid #eee; vertical-align: top; }}
  tr:hover td {{ background: #f8f8f8; }}
</style>
</head>
<body>
  <h2>LinkedIn Job Agent — {len(jobs)} nuevas ofertas</h2>
  <p style="color:#777;font-size:13px">Encontradas el {date_str} &nbsp;|&nbsp; Ordenadas por relevancia</p>
  <table>
    <tr>
      <th style="width:70px">Score</th>
      <th>Posición</th>
      <th style="width:160px">Ubicación</th>
      <th>Resumen IA</th>
      <th style="width:60px">Link</th>
    </tr>
    {rows}
  </table>
</body>
</html>"""

def notify_email(new_jobs: list, subject_prefix: str = ""):
    sender   = os.getenv("NOTIFY_EMAIL_FROM")
    receiver = os.getenv("NOTIFY_EMAIL_TO")
    password = os.getenv("GMAIL_APP_PASSWORD")

    if not sender or not password:
        log.warning("Email not configured, skipping notification.")
        return

    subject = f"{subject_prefix}🔔 {len(new_jobs)} nuevas ofertas LinkedIn"
    html = _build_html_email(new_jobs)

    msg = MIMEMultipart("alternative")
    msg["From"]    = sender
    msg["To"]      = receiver
    msg["Subject"] = subject
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(sender, password)
            server.sendmail(sender, receiver, msg.as_string())
        log.info("Email sent to %s", receiver)
    except Exception as e:
        log.error("Failed to send email: %s", e)

def notify_wishlist(wishlist_jobs: list):
    sender   = os.getenv("NOTIFY_EMAIL_FROM")
    receiver = os.getenv("NOTIFY_EMAIL_TO")
    password = os.getenv("GMAIL_APP_PASSWORD")
    if not sender or not password:
        return

    lines = ""
    for j in wishlist_jobs:
        lines += f"<li><strong>{j['title']}</strong> — {j['company']}<br>{j['location']}<br><a href='{j['url']}'>{j['url']}</a></li>"

    html = f"""<html><body style="font-family:Arial;padding:20px">
    <h2 style="color:#e74c3c">⭐ Empresa de tu wishlist encontrada!</h2>
    <ul>{lines}</ul>
    </body></html>"""

    msg = MIMEMultipart("alternative")
    msg["From"]    = sender
    msg["To"]      = receiver
    msg["Subject"] = f"⭐ WISHLIST: {', '.join(j['company'] for j in wishlist_jobs)}"
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(sender, password)
            server.sendmail(sender, receiver, msg.as_string())
        log.info("Wishlist alert sent for: %s", [j['company'] for j in wishlist_jobs])
    except Exception as e:
        log.error("Failed to send wishlist alert: %s", e)

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

        # AI summaries for new jobs
        if new_jobs:
            log.info("Generating AI summaries for %d jobs...", len(new_jobs))
            for j in new_jobs:
                j["summary"] = get_ai_summary(j.get("_desc", ""))

        for j in new_jobs:
            mark_seen(j)

        if first:
            mark_first_run_done()
            log.info("First run complete — %d jobs indexed, no email sent.", len(new_jobs))
        else:
            if new_jobs:
                # Wishlist company alert (immediate, separate email)
                wishlist = getattr(config, "WISHLIST_COMPANIES", [])
                wishlist_lower = [w.lower() for w in wishlist]
                wishlist_hits = [j for j in new_jobs if j["company"].lower() in wishlist_lower]
                if wishlist_hits:
                    log.info("WISHLIST MATCH: %s", [j['company'] for j in wishlist_hits])
                    notify_wishlist(wishlist_hits)

                notify_email(new_jobs)
                with open("new_jobs_latest.json", "w") as f:
                    json.dump(
                        [{k: v for k, v in j.items() if k != "_desc"} for j in new_jobs],
                        f, indent=2, ensure_ascii=False
                    )
                log.info("Results saved to new_jobs_latest.json")
            else:
                log.info("No new jobs found.")

    except Exception as e:
        log.error("Cycle error: %s", e, exc_info=True)

# ── REPORT ────────────────────────────────────────────────────────────────────

def get_all_jobs() -> list:
    con = sqlite3.connect(DB_PATH)
    rows = con.execute(
        "SELECT job_id, title, company, location, url, found_at, score, status FROM seen_jobs ORDER BY score DESC, found_at DESC"
    ).fetchall()
    con.close()
    return [
        {"id": r[0], "title": r[1], "company": r[2], "location": r[3],
         "url": r[4], "found_at": r[5], "score": r[6], "status": r[7]}
        for r in rows
    ]

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
        status_tag = f"[{j['status']}]" if j.get("status") != "new" else ""
        print(f"\n[{j.get('score', 0):3d}] {j['title']} — {j['company']} {status_tag}")
        print(f"  📍 {j['location']}")
        print(f"  🔗 {j['url']}")
        print(f"  📅 {j['found_at']}")

def send_db_report():
    jobs = get_all_jobs()
    if not jobs:
        log.info("No jobs in DB to report.")
        return
    sender   = os.getenv("NOTIFY_EMAIL_FROM")
    receiver = os.getenv("NOTIFY_EMAIL_TO")
    password = os.getenv("GMAIL_APP_PASSWORD")
    if not sender or not password:
        log.error("Email not configured.")
        return

    # Add placeholder summary for DB report
    for j in jobs:
        j["summary"] = f"Status: {j.get('status', 'new')}"

    html = _build_html_email(jobs)
    msg = MIMEMultipart("alternative")
    msg["From"]    = sender
    msg["To"]      = receiver
    msg["Subject"] = f"📋 LinkedIn DB Report — {len(jobs)} jobs"
    msg.attach(MIMEText(html, "html"))
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

    if "--set-status" in sys.argv:
        idx = sys.argv.index("--set-status")
        if len(sys.argv) > idx + 2:
            set_job_status(sys.argv[idx + 1], sys.argv[idx + 2])
        else:
            print("Usage: --set-status <job_id> <new|applied|interviewing|rejected|offer>")
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

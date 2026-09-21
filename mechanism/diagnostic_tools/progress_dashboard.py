#!/usr/bin/env python3
# mechanism/diagnostic_tools/progress_dashboard.py
"""
Read-only local progress dashboard for the 2026-09-18 Tiingo migration /
Russell 3000 universe expansion backfills.

Why a local HTTP server instead of a hosted Artifact: this progress data
(background job logs, live Postgres row counts) only exists on this
machine -- a hosted Artifact runs in a browser sandbox with no access to
localhost Postgres or local files, so it could only show "live" data if
something kept pushing updates into it. A tiny local server that recomputes
progress fresh on every page load is simpler and more genuinely live.

Does NOT touch, pause, or interact with the backfill jobs in any way --
purely reads their log files and queries the DB with read-only SELECTs.

Usage:
    python progress_dashboard.py [--port 8899]
Then open http://localhost:8899 in a browser. The page auto-refreshes
every 5 seconds.

NOTE: the three JOB_LOGS paths below are specific to the Claude Code task
IDs from the 2026-09-18 session that launched these backfills -- update
them (or pass --log) if re-running this against different jobs.
"""
import sys
import os
import re
import argparse
import http.server
import socketserver
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from shared import db

TASK_DIR = r"C:\Users\Daniel\AppData\Local\Temp\claude\e---------pythonProjects-donchian-screener-0-1\1a5d56bf-1200-467f-91b0-8a78e7b8bccd\tasks"

JOBS = [
    {
        'name': 'Quarterly fundamentals backfill (original 1,005 symbols)',
        'log': os.path.join(TASK_DIR, 'b00dk5x95.output'),
        'done_marker': 'Quarterly fundamentals update completed',
    },
    {
        'name': '10-year price repopulation (original 1,005 symbols)',
        'log': os.path.join(TASK_DIR, 'b3f9gbjt5.output'),
        'done_marker': 'Tiingo repopulation completed',
    },
    {
        'name': '10-year price onboarding (2,109 new Russell 3000 symbols)',
        'log': os.path.join(TASK_DIR, 'b36ac3jip.output'),
        'done_marker': 'Tiingo repopulation completed',
    },
    {
        'name': 'Quarterly fundamentals onboarding (2,113 new Russell 3000 symbols)',
        'log': os.path.join(TASK_DIR, 'bxoo8gjxm.output'),
        'done_marker': 'Quarterly fundamentals update completed',
    },
]

PROGRESS_RE = re.compile(r'Processing (\S+) \((\d+)/(\d+)\)')
TIMESTAMP_RE = re.compile(r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})')


def parse_job_progress(job):
    """Read a job's log tail and extract current/total + ETA. Read-only."""
    result = {
        'name': job['name'], 'current': 0, 'total': 0, 'pct': 0.0,
        'current_symbol': None, 'done': False, 'eta_min': None,
        'first_ts': None, 'last_ts': None,
    }
    if not os.path.exists(job['log']):
        result['error'] = 'log file not found'
        return result

    try:
        with open(job['log'], encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
    except Exception as e:
        result['error'] = str(e)
        return result

    if any(job['done_marker'] in line for line in lines):
        result['done'] = True

    first_ts = None
    last_progress_ts = None
    for line in lines:
        ts_match = TIMESTAMP_RE.match(line)
        if ts_match:
            if first_ts is None:
                first_ts = ts_match.group(1)
        m = PROGRESS_RE.search(line)
        if m:
            result['current_symbol'] = m.group(1)
            result['current'] = int(m.group(2))
            result['total'] = int(m.group(3))
            if ts_match:
                last_progress_ts = ts_match.group(1)

    result['first_ts'] = first_ts
    result['last_ts'] = last_progress_ts

    if result['total']:
        result['pct'] = min(100.0, result['current'] / result['total'] * 100)
    if result['done']:
        result['pct'] = 100.0

    # ETA from elapsed-time-per-symbol so far
    if first_ts and last_progress_ts and result['current'] and not result['done']:
        try:
            fmt = '%Y-%m-%d %H:%M:%S'
            elapsed_sec = (datetime.strptime(last_progress_ts, fmt) - datetime.strptime(first_ts, fmt)).total_seconds()
            if elapsed_sec > 0 and result['current'] > 0:
                rate = elapsed_sec / result['current']
                remaining = result['total'] - result['current']
                result['eta_min'] = round(rate * remaining / 60, 1)
        except Exception:
            pass

    return result


def get_db_stats():
    """Read-only DB stats -- plain SELECTs, no writes."""
    stats = {}
    try:
        r = db.execute_dict_query("SELECT pg_size_pretty(pg_database_size(current_database())) AS size")
        stats['db_size'] = r[0]['size']
        r = db.execute_dict_query("SELECT COUNT(DISTINCT symbol) AS n FROM stock_prices")
        stats['symbols_with_prices'] = r[0]['n']
        r = db.execute_dict_query("SELECT COUNT(*) AS n FROM stock_prices")
        stats['stock_prices_rows'] = r[0]['n']
        r = db.execute_dict_query("SELECT COUNT(*) AS n FROM technical_indicators")
        stats['technical_indicators_rows'] = r[0]['n']
        r = db.execute_dict_query("SELECT COUNT(DISTINCT symbol) AS n FROM quarterly_fundamentals")
        stats['symbols_with_quarterly'] = r[0]['n']
    except Exception as e:
        stats['error'] = str(e)
    return stats


def bar(pct, done):
    color = '#22c55e' if done else '#3b82f6'
    return f'''<div class="track"><div class="fill" style="width:{pct:.1f}%;background:{color}"></div></div>'''


def render_page():
    jobs_html = []
    for job in JOBS:
        p = parse_job_progress(job)
        status = 'DONE' if p['done'] else (f"{p['current_symbol']}" if p['current_symbol'] else 'starting...')
        eta = f"~{p['eta_min']:.0f} min left" if p.get('eta_min') and not p['done'] else ('' if not p['done'] else 'complete')
        jobs_html.append(f'''
        <div class="job">
          <div class="job-head">
            <span class="job-name">{job['name']}</span>
            <span class="job-count">{p['current']}/{p['total']} ({p['pct']:.1f}%)</span>
          </div>
          {bar(p['pct'], p['done'])}
          <div class="job-sub">{status} &middot; {eta}</div>
        </div>''')

    db_stats = get_db_stats()
    db_html = f'''
        <div class="stat"><span class="stat-label">Database size</span><span class="stat-val">{db_stats.get('db_size', '?')}</span></div>
        <div class="stat"><span class="stat-label">Symbols with price history</span><span class="stat-val">{db_stats.get('symbols_with_prices', '?'):,}</span></div>
        <div class="stat"><span class="stat-label">stock_prices rows</span><span class="stat-val">{db_stats.get('stock_prices_rows', 0):,}</span></div>
        <div class="stat"><span class="stat-label">technical_indicators rows</span><span class="stat-val">{db_stats.get('technical_indicators_rows', 0):,}</span></div>
        <div class="stat"><span class="stat-label">Symbols with quarterly fundamentals</span><span class="stat-val">{db_stats.get('symbols_with_quarterly', '?'):,}</span></div>
    ''' if 'error' not in db_stats else f'<div class="stat error">DB error: {db_stats["error"]}</div>'

    all_done = all(parse_job_progress(j)['done'] for j in JOBS)
    banner = '<div class="banner done">All backfills complete</div>' if all_done else ''

    return f'''<!doctype html>
<html><head>
<meta charset="utf-8"><meta http-equiv="refresh" content="5">
<title>Backfill Progress</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, sans-serif; background:#0f172a; color:#e2e8f0; margin:0; padding:24px; }}
  h1 {{ font-size:18px; font-weight:600; margin:0 0 4px; }}
  .updated {{ color:#64748b; font-size:12px; margin-bottom:20px; }}
  .job {{ background:#1e293b; border-radius:8px; padding:14px 16px; margin-bottom:12px; }}
  .job-head {{ display:flex; justify-content:space-between; font-size:13px; margin-bottom:8px; }}
  .job-name {{ font-weight:500; }}
  .job-count {{ color:#94a3b8; font-variant-numeric:tabular-nums; }}
  .track {{ background:#334155; border-radius:6px; height:10px; overflow:hidden; }}
  .fill {{ height:100%; border-radius:6px; transition:width 0.3s; }}
  .job-sub {{ color:#64748b; font-size:11px; margin-top:6px; }}
  .section {{ background:#1e293b; border-radius:8px; padding:14px 16px; margin-top:20px; }}
  .section-title {{ font-size:13px; font-weight:600; margin-bottom:10px; color:#94a3b8; text-transform:uppercase; letter-spacing:0.05em; }}
  .stat {{ display:flex; justify-content:space-between; font-size:13px; padding:4px 0; border-bottom:1px solid #334155; }}
  .stat:last-child {{ border-bottom:none; }}
  .stat-label {{ color:#94a3b8; }}
  .stat-val {{ font-variant-numeric:tabular-nums; font-weight:500; }}
  .banner {{ padding:10px 14px; border-radius:8px; margin-bottom:16px; font-size:13px; font-weight:500; }}
  .banner.done {{ background:#14532d; color:#86efac; }}
</style>
</head><body>
<h1>Russell 3000 / Tiingo Migration Backfill Progress</h1>
<div class="updated">Auto-refreshes every 5s &middot; last checked {datetime.now().strftime('%H:%M:%S')}</div>
{banner}
{''.join(jobs_html)}
<div class="section">
  <div class="section-title">Database</div>
  {db_html}
</div>
</body></html>'''


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            page = render_page().encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(page)))
            self.end_headers()
            self.wfile.write(page)
        except Exception as e:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(str(e).encode())

    def log_message(self, format, *args):
        pass  # quiet -- don't spam the console on every 5s refresh


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8899)
    args = parser.parse_args()

    with socketserver.TCPServer(("127.0.0.1", args.port), Handler) as httpd:
        print(f"Progress dashboard running at http://localhost:{args.port}")
        print("Press Ctrl+C to stop (read-only -- does not affect the backfill jobs)")
        httpd.serve_forever()

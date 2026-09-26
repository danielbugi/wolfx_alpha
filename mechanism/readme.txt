mechanism/ — see the authoritative docs instead of this file
==============================================================

This file described a snapshot of the system from July 2025: `automation/` as the pipeline directory
name (renamed to `mechanism/` since), `master_automation_runner_updated.py` (superseded, not wired
into the live pipeline), a local-Postgres-only setup with no VPS, and record counts/table shapes that
have long since changed. Replaced 2026-09-27 rather than kept in sync, since a second, drifting copy
of this information is exactly what caused it to go stale in the first place.

Current, accurate documentation:
- docs/architecture/SYSTEM_OVERVIEW.md — the primary technical map: production architecture, the 5
  execution paths, component ownership, "where should new work go." Start here.
- docs/architecture/CODEBASE_AUDIT.md, "### mechanism/ (excl. alerts/)" and "### mechanism/alerts/" —
  per-file classification (ACTIVE/LEGACY/DEAD, with evidence).
- docs/architecture/DATABASE.md — the real, current schema by functional domain.
- docs/architecture/TELEGRAM_PUBLISHING.md — everything under mechanism/alerts/.
- docs/architecture/SCHEDULING.md — what actually triggers mechanism/ code in production (VPS
  systemd timers, not Windows Task Scheduler).
- CLAUDE.md — the concise entry point; routes to all of the above.

The pre-2026-09-27 version of this file, if you need the historical detail, is in git history.

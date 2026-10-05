"""Forward collection (Slice 7, LAB ONLY, DORMANT): one orchestration entry point for the per-session research observations.

Nothing here is installed, scheduled, imported by the pipeline, or applied anywhere. It composes writers that already exist (the Market
Intelligence runner for market + sector + per-stock relative strength, and the fwd_v1 label runner) under one explicit per-session contract:
which session, in which order, under which lock, with which deadline, and what a half-finished session looks like. It invents no new
research table, no new feature and no historical backfill. See docs/research/LAB_SLICE7_COLLECTOR_READINESS.md.

    contract.py       pure: steps, order, per-source collector contract, session classification, scheduler design, no-sector policy
    orchestrator.py   runs the steps for ONE explicit session (advisory lock, deadline guard, bounded retry, never fakes success)
    steps.py          the composition root: the ONLY module that imports the writers
    preflight.py      read-only "collector stack ready for activation: YES/NO"
    cli.py            python -m forward_collection run|preflight   (dry-run by default)
"""

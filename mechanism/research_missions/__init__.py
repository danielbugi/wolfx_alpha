"""Research Missions: a bounded research question answered with cited, verified evidence (docs/product/RESEARCH_MISSIONS_MVP_DESIGN.md).

Every paid model call goes through research_missions.llm.Gateway, which reserves the worst-case cost against the budget ledger before
the request and settles the actual cost after it. Nothing here sends a notification or runs on a schedule.
"""

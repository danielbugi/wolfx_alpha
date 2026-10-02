"""Market Intelligence: strategy-neutral, session-level market context (risk regime, sector / relative-strength snapshots,
catalyst-event model). Nothing here is a Donchian eligibility rule, a screener input or a score input. Pure computation modules
(`risk_regime`, `relative_strength`, `events`, `provenance`) import no database driver and read no clock; only `store` talks to a database."""

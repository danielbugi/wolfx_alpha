# Evidence: the 2026-10-02 universe-guards boundary inside `donchian_breakout v1` (c1 → c2)

Collected 2026-10-22 (read-only) for Proposal B (`docs/research/db_research/PROPOSAL_B_STRATEGY_CONTRACTS_C1_C2.md`).
- **Why:** the journal rotates (retention currently starts 2026-09-24 22:01Z), and the env file is mutable. This copy preserves what they showed on the collection day.
- **Redaction:** the hostname is replaced by `<host>`. From the env files, only the one setting `GUARDS_EFFECTIVE_FROM` (not a secret) plus file names and mtimes is recorded. No other configuration value is copied.

| File | Source | What it shows |
|---|---|---|
| `journal_guards_mode_lines.txt` | `journalctl -u donchian-pipeline.service --since 2026-09-27 \| grep "Universe guards mode"` (13 lines) | The guards ran from session 2026-10-02 (first line 2026-10-02T23:15:48Z). One line per session through 2026-10-21. The 2026-09-28 and 2026-10-01 runs (~200k journal lines each) contain no guards line. |
| `config_evidence.txt` | `/opt/donchian/env/.env*` (name=value of the one setting, plus mtimes) | `.env.bak.20261002T172757Z`, taken before an edit, lacks the setting. `.env.bak.pre-S10-20261003T094542Z` (mtime 2026-10-02 17:35:34Z) has it. The setting was therefore added on **2026-10-02 between 17:27:57Z and 17:35:34Z**, before that session's pipeline run (23:15Z). |
| `ledger_cohort_markers.txt` | Read-only SQL on `signal_ledger`, `research_capture_activation`, `candidate_observation` | Per-cohort row counts, the `model_version='unknown'` side-effect marker (10-02 to 10-06), and the first session with immutable per-candidate guard records (2026-10-09). |

**Limits:**
- File mtimes and the journal are operational evidence, not cryptographic proof.
- No per-signal configuration value was recorded in the database before 2026-10-09.

## sha256 (of the files as committed, LF line endings)
```
7a3322aa2524e1846c6bb8e4458eb6cf2ca01d98fa56abca43a434ce7d82b5a8 *journal_guards_mode_lines.txt
6fa699aad62813aade2ac7771beb2e63bfb094143fd3f336098386007519b897 *config_evidence.txt
48051df245a86aeb5847ac73c5ee14f0369fe011658fc83b3ece2432f93e43a0 *ledger_cohort_markers.txt
```

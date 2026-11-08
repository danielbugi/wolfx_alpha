# Fundamental Updates: night selection policy and simulation (revised)

2026-11-07, revised 2026-11-08 (heuristic renamed and documented; results unchanged). **Read-only simulation. Nothing was sent, merged into a schedule, or written to production.**
Code: `mechanism/inflection/update_select.py` (pure) and `update_simulate.py` (replay); 37 tests in `mechanism/inflection/tests/test_update_select.py`.
Input: the read-only export `docs/product/fu_selection_simulation.sql`, which now also carries the quarter's feature metrics (`fundamental_feature.metrics`).

## Policy (as approved by the owner on 2026-11-07)
**Limits**
- At most **3 posts a night**.
- At most **1 post per company**.
- At most **2 posts with the same lead event type**.
- **Empty slots are allowed:** no rule is relaxed to fill a slot.

**Eligibility** (unchanged from the integration review)
- Public type.
- SEC acceptance or filing date.
- Known filing accession.
- Point-in-time rules: detected on or after the public date, and at most 104 days after quarter end.
- Public within 3 days of the session (the backlog guard).
- Market cap at publication of at least $300M.
- No repeat of a company within 30 days.

**Operational support:** four signals, read from the **same quarter's** feature metrics. That is the filing the event came from, so nothing later can enter. A missing input counts as absent, never as present; banks and insurers, for example, report no operating income.

| Signal | Rule |
|---|---|
| operating income | above the prior-year quarter (prior year = prior-year operating margin × prior-year revenue) |
| operating margin | up at least 50 bps year over year |
| revenue growth | at least +2% year over year |
| cash flow | free-cash-flow margin up at least 1 point year over year |

**Non-operating divergence heuristic.** This is a proxy, **not proof of a nonrecurring item**. It measures the share of the year-over-year net-income improvement **not** matched by an operating-income improvement.
- **Formula:** `non_operating_divergence = (ΔNI − ΔOI) / ΔNI`.
  - **ΔNI:** this quarter's net income minus the prior-year quarter's. It must be above 0.
  - **ΔOI:** this quarter's operating income minus the prior-year quarter's, where prior-year operating income = prior-year operating margin × prior-year revenue. Prior-year revenue must be above 0.
- **Every input must be a finite number.** Otherwise the heuristic is *undefined*, and it then holds or demotes nothing.
- **Reading the value:**
  - 0 or less: operating income explains all of the improvement.
  - 1: operating income explains none of it.
  - Above 1: operating income fell while net income rose.

  The remainder came from below the operating line: gains on sales, revaluations, tax effects, interest or discontinued operations.

**A net-income turnaround with a divergence above 0.5:**
- does not count as an inflection;
- cannot lead a post if the company has another inflection;
- holds the company (no post) if it is the company's only basis.

It judges **only** net-income turnarounds. A margin-, revenue- or cash-led company is never held or flagged by it.

**Limitations:**
- The source data (normalized quarterly statements) does not itemize nonrecurring items. The heuristic only sees which line moved.
- **Tax effect:** operating income is pre-tax and net income is after tax. A fully operational improvement taxed at rate *t* shows a **negative** divergence of about −t/(1−t), about −0.27 at 21%. The heuristic therefore leans toward calling an improvement operational, never the other way.
- **Blind spot:** a one-off item *inside* operating income, such as a litigation settlement, a restructuring charge or an asset sale booked as operating, is invisible to it.
- **False flag:** a recurring below-the-line item, such as a steady equity-method stake or lower interest after debt repayment, counts as divergence.
- **Rounding:** prior-year operating income is derived from the stored margin and revenue, so rounding in either carries through.
- **Small changes:** a tiny ΔNI makes the ratio extreme in either direction. Only the threshold is used, never the size, so this cannot change a ranking.
- **Banks and insurers:** companies without operating income are undefined. The heuristic never holds them, and their operating signals count as absent.

**Edge cases tested** (`test_non_operating_divergence_edge_cases` and three further tests):
- an operating loss that narrowed while net income turned positive;
- operating income turning positive;
- operating income falling into a loss while net income turned positive;
- both still losses;
- unchanged operating income;
- a fully operational, taxed improvement;
- net income that fell or was unchanged;
- zero or negative prior-year revenue;
- missing operating income;
- infinite, NaN and boolean inputs;
- the exact 0.5 boundary (not held) versus 0.52 (held);
- undefined input never holding or flagging;
- non-net-income leads never being held.

**Ranking** (editorial salience, not a forecast):
1. Supported preferred families: net income, operating margin or leverage, revenue.
2. Operational support signals (0–4).
3. Supported independent families: the preferred three plus free cash flow.
4. Lead type: net income > operating margin > revenue > operating leverage > free cash flow.
5. Size of the lead change, **capped at 50 points**: above the cap, size no longer raises the rank.
6. Market cap, then symbol.

**No predictive investment score.** Prices, returns, market reaction, `strength`, `severity` and the inflection score are not inputs. A test changes all of them and asserts that the selection is identical.

## Results, sessions Nov 2–6 (inflection_v2_sec_pit)
| Session | New events | Excluded (reason: count) | Held (non-operating divergence) | Eligible companies | Posts (previous rules) |
|---|---|---|---|---|---|
| Mon Nov 2 | 2,116 (first run: backlog) | backlog 1,891 · limited-repricing type 148 · >104 days 5 · size 4 | SMP | 45 | **FET, PTEN, KRYS** (FET, PTEN, CVEO) |
| Tue Nov 3 | 1,011 (coverage expanded) | backlog 863 · limited-repricing type 65 · >104 days 2 · size 1 | — | 47 | **ICHR, LITE, CE** (ICHR, LITE, MQ) |
| Wed Nov 4 | 118 | size 4 · backlog 2 · limited-repricing type 1 | — | 67 | **VSH, SBGI, CORT** (VSH, SBGI, MSGE) |
| Thu Nov 5 | 214 | size 9 · limited-repricing type 2 · backlog 1 | QCOM, ROCK | 114 | **AMC, U, VSEC** (AMC, CORZ, BKSY) |
| Fri Nov 6 | 150 | size 12 · explanation inputs missing 1 | — | 90 | **GTN, ETON, CENX** (GTN, ETON, SVV) |

**Lead types:** net income 10, operating margin 5 (was 14 net income, 1 margin). No night has more than 2 of one type. All 15 slots were filled, because enough eligible candidates existed every night.

### The 15 selected posts
Support order: operating income / operating margin / revenue growth / cash flow; `+` = met, `−` = not met.

| Session | Company | Lead (other inflections) | Key figures | Support | Non-op. divergence | Mkt cap | Public |
|---|---|---|---|---|---|---|---|
| Nov 2 | FET Forum Energy Technologies | Net income turned positive (margin, revenue) | NI $11.2M; swing 13.4% of revenue | ++++ | 0.18 | $955M | Oct 30 |
| Nov 2 | PTEN Patterson-UTI | Net income turned positive (margin, revenue) | NI $28.4M; swing 4.8% | ++++ | −0.41 | $4.48B | Nov 2 |
| Nov 2 | KRYS Krystal Biotech | Operating margin expanded (op. leverage, revenue) | margin 55.9%, +13.6 pts; revenue +30.2% | ++++ | — | $8.76B | Nov 2 |
| Nov 3 | ICHR Ichor | Net income turned positive (margin, revenue) | NI $20.9M; swing 13.3% | +++− | 0.04 | $1.37B | Nov 3 |
| Nov 3 | LITE Lumentum | Operating margin expanded (FCF, op. leverage, revenue) | margin 28.5%, +27.2 pts; revenue +136.5% | ++++ | 0.01 | $61.7B | Nov 3 |
| Nov 3 | CE Celanese | Net income turned positive (margin) | NI $74.0M; swing 56.0% (extreme, operationally backed) | ++++ | −0.03 | $5.53B | Nov 3 |
| Nov 4 | VSH Vishay | Net income turned positive (FCF, margin, op. leverage, revenue) | NI $59.0M; swing 6.8% | ++++ | −0.09 | $3.96B | Nov 4 |
| Nov 4 | SBGI Sinclair | Net income turned positive (FCF, margin, op. leverage, revenue) | NI $43.0M; swing 4.9% | ++++ | −0.73 | $1.02B | Nov 4 |
| Nov 4 | CORT Corcept | Operating margin expanded (op. leverage, revenue) | margin 33.7%, +28.8 pts; revenue +64.0% | ++++ | 0.02 | $8.86B | Nov 3 |
| Nov 5 | AMC AMC Entertainment | Net income turned positive (FCF, margin, op. leverage, revenue) | NI $109.3M; swing 23.8% | ++++ | 0.48 | $3.14B | Nov 4 |
| Nov 5 | U Unity Software | Net income turned positive (margin, revenue) | NI $121.2M; swing 39.8% | ++++ | 0.09 | $21.5B | Nov 5 |
| Nov 5 | VSEC VSE | Operating margin expanded (op. leverage, revenue) | margin 14.3%, +10.8 pts; revenue +91.2% | ++++ | −0.55 | $4.62B | Nov 5 |
| Nov 6 | GTN Gray Media | Net income turned positive (FCF, margin, op. leverage, revenue) | NI $78.0M; swing 9.1% | ++++ | −0.86 | $507M | Nov 6 |
| Nov 6 | ETON Eton Pharmaceuticals | Net income turned positive (margin, revenue) | NI $35.2M; swing 64.2% (extreme, operationally backed) | ++++ | 0.34 | $1.35B | Nov 5 |
| Nov 6 | CENX Century Aluminum | Operating margin expanded (FCF, op. leverage, revenue) | margin 30.4%, +21.6 pts; revenue +52.0% | ++++ | 0.18 | $2.98B | Nov 5 |

A negative divergence means operating income improved by more than net income did, so the improvement is fully operational.

### What changed and why
| Company | Before | Now | Reason |
|---|---|---|---|
| QCOM | (outranked) | **held** | Year-ago net loss −$3.12B against about +$2.9B of operating income: the loss came from below the operating line. Operating income actually fell (to $1.36B), so the divergence is 1.36 |
| SMP, ROCK | (outranked) | **held** | divergence 0.86 / 0.91: most of the net-income improvement is not matched by operating income, and it was their only inflection |
| CORZ | selected (+568 pts) | outranked, led by its margin event | divergence 0.90: net income no longer counts or leads |
| BKSY | selected (+79 pts) | type cap | Third net-income lead that night; operating support unknown for cash flow |
| MQ, SVV | selected | outranked | divergence 0.86 / 0.75; their remaining operating inflection ranks lower |
| TFX, GLIBA, NVST | outranked | outranked | Extreme swings no longer gain from their size (cap at 50 points) |
| CVEO, MSGE | selected | type cap | Third net-income lead that night |
| KRYS, CORT, VSEC, CENX | outranked | selected | Operating-margin leaders with full operational support take the third slot |
| CE, U | outranked | selected | Net-income turnarounds fully backed by operating income |

**Extreme swings** (above 50 points of revenue): 14 eligible companies.
- **Selected (2):** CE and ETON. Both are backed by operating income (divergence −0.03 and 0.34) and all four support signals.
- **The other 12 are not selected:**
  - type-capped: BKSY;
  - outranked: ZVRA, MQ, LTC, NVST, ECHO, CORZ, ENOV, TAP, TFX, COLD and GLIBA.

## Open points for the owner
1. **Net income still leads 10 of 15 posts.** The rules allow it, at 2 per night maximum. A cap of 1 per type, simulated the same way, gives every night one net-income, one margin and one revenue lead:

   | Session | Posts with a cap of 1 per type |
   |---|---|
   | Nov 2 | FET, KRYS, BLZE |
   | Nov 3 | ICHR, LITE, IBTA |
   | Nov 4 | VSH, CORT, SMRT |
   | Nov 5 | AMC, VSEC, ARW |
   | Nov 6 | GTN, CENX, AVT |

   That is a policy choice, and the code supports it via `max_per_type`.
2. **ETON** is selected on Nov 6 for its September quarter, which is correct and operationally backed. It was one of the 16 misleading owner alerts, for its June quarter, so the name may look familiar.
3. **The non-operating divergence heuristic is a proxy, not proof of a nonrecurring item.** Its limitations are listed above. A held company is "not operationally supported by its filing's figures", never "a one-off". Itemized nonrecurring facts would need SEC XBRL line items, which are not applied to production (migration 38).
4. **Thresholds are owner-adjustable constants:** 50 bps, +2%, +1 point, 0.5 divergence, 50-point cap and 2 per type. They were chosen for plain meaning, not fitted to outcomes.

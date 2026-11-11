"""Renders result.json as markdown tables (every number comes from the result; nothing is typed by hand).

    PYTHONPATH=mechanism python -m research.right_tail.report OUT/result.json > tables.md
"""
from __future__ import annotations

import json
import sys
from typing import Any, Dict, List

from research.right_tail.study import EXITS

SETS_ORDER = ["D55", "D55+RS", "D55+VOL", "D55+RS+VOL", "D55+RS+VOL+REGIME", "BASE", "BASE+RS", "BASE+VOL", "BASE+RS+VOL", "BASE+RS+VOL+REGIME"]


def _f(x, nd=3, pct=False):
    if x is None:
        return "–"
    return f"{x * 100:.1f}%" if pct else f"{x:+.{nd}f}" if isinstance(x, float) else str(x)


def _ci(c):
    return f"[{c[0]:+.3f}, {c[1]:+.3f}]" if c else "–"


def cell_row(name: str, c: Dict[str, Any]) -> str:
    if not c.get("n"):
        return f"| {name} | 0 |" + " – |" * 9
    s = c["share_ge"]
    return (f"| {name} | {c['n']} | {_f(c['expectancy_r'])} | {_ci(c['ci95'])} | {_f(c['median_r'], 2)} | {_f(c['win_rate'], pct=True)} | "
            f"{_f(s['3R'], pct=True)} / {_f(s['5R'], pct=True)} / {_f(s['10R'], pct=True)} / {_f(s['20R'], pct=True)} | "
            f"{_f(c['avg_winner_r'], 2)} / {_f(c['avg_loser_r'], 2)} | {_f(c['initial_stop_out_share'], pct=True)} | {c['duration_bars']['median']:.0f} | {c['max_r']:+.1f} |")


HDR = ("| cell | n | mean net R | 95% CI | median | win | ≥3R / 5R / 10R / 20R | avg win / loss R | initial-stop exits | median bars | max R |\n"
       "|---|---|---|---|---|---|---|---|---|---|---|")


def render(res: Dict[str, Any]) -> str:
    L: List[str] = []
    cells = res["cells"]
    p = res["primary"]
    L.append(f"spec `{res['spec_hash'][:12]}` · data `{res['data_hash'][:12]}` · code `{res['code_ref']}` · seed {res['seed']} · symbols used {res['symbols_used']} of {res['symbols_loaded']}\n")
    L.append(f"## Primary cell: {p['set']} · {p['exit']} · {int(p['cost_bps'])} bps → **{p['verdict']}**\n")
    L.append(HDR)
    for seg in ("discovery", "validation"):
        L.append(cell_row(f"{p['set']} {seg}", p["cells"][seg]))
        L.append(cell_row(f"random-entry control {seg}", p["cells"][f"random_{seg}"]))
    L.append("")
    for seg in ("discovery", "validation"):
        d = p["minus_random"][seg]
        L.append(f"- signal minus random ({seg}): {_f(d.get('diff'))} R, 95% CI {_ci(d.get('ci95'))}")
    L.append("")
    for c in p["decision_checks"]:
        L.append(f"- {'PASS' if c['ok'] else 'FAIL'} — {c['check']}: `{c['value']}`")
    for seg in ("discovery", "validation"):
        L.append(f"\n## All entry sets × exits — {seg}, 20 bps (only the primary cell decides; the Bonferroni interval covers 50 cells)\n")
        L.append("| set | exit | n | mean net R | 95% CI | Bonferroni CI | ≥5R | ≥10R | ≥20R | median bars | without top 1% |")
        L.append("|---|---|---|---|---|---|---|---|---|---|---|")
        for s in SETS_ORDER:
            for k in EXITS:
                c = cells.get(f"{s}|{k}|{seg}|20bps", {})
                if not c.get("n"):
                    L.append(f"| {s} | {k} | 0 | | | | | | | | |")
                    continue
                L.append(f"| {s} | {k} | {c['n']} | {_f(c['expectancy_r'])} | {_ci(c['ci95'])} | {_ci(c['ci_bonferroni'])} | {_f(c['share_ge']['5R'], pct=True)} | "
                         f"{_f(c['share_ge']['10R'], pct=True)} | {_f(c['share_ge']['20R'], pct=True)} | {c['duration_bars']['median']:.0f} | {_f(c['expectancy_without_top_1pct'])} |")
        for k in EXITS:
            c = cells.get(f"RANDOM(D55)|{k}|{seg}|20bps", {})
            if c.get("n"):
                L.append(f"| random entry (D55 stop) | {k} | {c['n']} | {_f(c['expectancy_r'])} | {_ci(c['ci95'])} | – | {_f(c['share_ge']['5R'], pct=True)} | "
                         f"{_f(c['share_ge']['10R'], pct=True)} | {_f(c['share_ge']['20R'], pct=True)} | {c['duration_bars']['median']:.0f} | {_f(c['expectancy_without_top_1pct'])} |")
    L.append("\n## Costs sensitivity (validation, mean net R at 10 / 20 / 40 bps)\n")
    L.append("| set | exit | 10 bps | 20 bps | 40 bps |\n|---|---|---|---|---|")
    for s in ("D55", "BASE", "BASE+RS+VOL"):
        for k in EXITS:
            v = [cells.get(f"{s}|{k}|validation|{b}bps", {}).get("expectancy_r") for b in (10, 20, 40)]
            L.append(f"| {s} | {k} | " + " | ".join(_f(x) for x in v) + " |")
    L.append("\n## MFE versus what was realised (primary exit X4, 20 bps)\n")
    L.append("| set | segment | mean MFE while held (R) | theoretical max within 250 bars (R, mature) | ≥5R / ≥10R reached (theoretical) | realised gross on the same positions | MAE mean / p90 (R) |")
    L.append("|---|---|---|---|---|---|---|")
    for s in ("D55", "BASE", "BASE+RS+VOL"):
        for seg in ("discovery", "validation"):
            c = cells.get(f"{s}|X4_trend_sma50|{seg}|20bps", {})
            if not c.get("n"):
                continue
            t = c["theoretical_mfe250"]
            L.append(f"| {s} | {seg} | {c['mfe_r_mean_in_trade']:.2f} | {_f(t['mean_r'], 2)} (n {t['n_mature']}) | {_f(t['share_ge_5R'], pct=True)} / {_f(t['share_ge_10R'], pct=True)} | "
                     f"{_f(t['realised_gross_mean_same_positions'], 2)} | {c['mae_r_mean']:.2f} / {c['mae_r_p90']:.2f} |")
    L.append("\n## Execution: gaps and entry slippage (20 bps cells)\n")
    L.append("| set | exit | segment | stop exits that gapped | extra loss when gapped (R) | gap cost per position (R) | entry open vs signal close (R) | median initial risk % |")
    L.append("|---|---|---|---|---|---|---|---|")
    for s in ("D55", "BASE+RS+VOL"):
        for k in ("X1_stop_only", "X4_trend_sma50"):
            for seg in ("discovery", "validation"):
                c = cells.get(f"{s}|{k}|{seg}|20bps", {})
                if c.get("n"):
                    g = c["gap_through_stop"]
                    L.append(f"| {s} | {k} | {seg} | {_f(g['share_of_stop_exits'], pct=True)} | {_f(g['mean_extra_loss_r_when_gapped'], 2)} | {_f(g['mean_over_all_positions_r'], 3)} | "
                             f"{_f(c['entry_gap_r_mean'], 3)} | {c['initial_risk_pct_median']:.1f} |")
    L.append("\n## Portfolio (max 20 concurrent, 0.5% of initial equity risked per position, discovery + validation, 20 bps)\n")
    L.append("| set | exit | taken | rejected (capacity) | total R | max drawdown R | max DD % | return %/yr |")
    L.append("|---|---|---|---|---|---|---|---|")
    for s in ("D55", "D55+RS+VOL", "BASE", "BASE+RS+VOL", "BASE+RS+VOL+REGIME"):
        for k in EXITS:
            q = res["portfolio"].get(f"{s}|{k}")
            if q:
                L.append(f"| {s} | {k} | {q['positions_taken']} | {q['signals_rejected_capacity']} | {q['total_r']:+.1f} | {q['max_drawdown_r']:.1f} | {q['max_drawdown_pct_at_risk']:.1f} | {q['return_pct_per_year_at_risk']:+.2f} |")
    L.append("\n## Market conditions (descriptive, 20 bps)\n")
    for key, segs in res["regime"].items():
        L.append(f"**{key}**\n")
        L.append("| segment | bucket | n | mean net R | ≥5R |\n|---|---|---|---|---|")
        for seg, d in segs.items():
            for kind in ("regime", "breadth"):
                for b, v in d[kind].items():
                    L.append(f"| {seg} | {b} | {v['n']} | {v['expectancy_r']:+.3f} | {v['share_ge_5R'] * 100:.1f}% |")
        L.append("")
    L.append("## By signal year (20 bps)\n")
    L.append("| cell | " + " | ".join(str(y) for y in range(2017, 2027)) + " |")
    L.append("|---|" + "---|" * 10)
    for key, d in res["years"].items():
        L.append(f"| {key} | " + " | ".join(f"{d[str(y)]['expectancy_r']:+.2f} ({d[str(y)]['n']})" if str(y) in d else "–" for y in range(2017, 2027)) + " |")
    L.append("\n## Censoring and data-validity sensitivities (20 bps)\n")
    L.append("| cell | segment | primary n / mean | with path-discontinuity trades | censored marked to last close |")
    L.append("|---|---|---|---|---|")
    for s in ("D55", "BASE+RS+VOL"):
        for k in ("X1_stop_only", "X4_trend_sma50"):
            for seg in ("discovery", "validation"):
                c = cells.get(f"{s}|{k}|{seg}|20bps", {})
                z = res["sensitivity"][f"{s}|{k}|{seg}"]
                L.append(f"| {s} {k} | {seg} | {c.get('n')} / {_f(c.get('expectancy_r'))} (censored {c.get('n_censored')}) | {z['include_path_discontinuities']['n']} / "
                         f"{_f(z['include_path_discontinuities']['expectancy_r'])} | {z['censored_marked_to_last_close']['n']} / {_f(z['censored_marked_to_last_close'].get('expectancy_r'))} |")
    L.append("\n## What distinguished realised ≥5R winners (DISCOVERY only, descriptive)\n")
    for key, w in res["winners_vs_rest_discovery"].items():
        if "features" not in w:
            continue
        L.append(f"**{key}** — n {w['n']}, ≥5R {w['n_ge_5R']} (base rate {w['base_rate'] * 100:.1f}%)\n")
        L.append("| feature | median ≥5R | median rest | ≥5R rate low / mid / high tercile |\n|---|---|---|---|")
        for f, v in w["features"].items():
            t = v["terciles"]
            L.append(f"| {f} | {v['median_winners']} | {v['median_rest']} | " + " / ".join(f"{t[k]['rate_ge_5R'] * 100:.1f}%" for k in ("low", "mid", "high")) + " |")
        L.append("")
    L.append("## Largest realised trades (sanity check for data errors)\n")
    for key, rows in res["top_trades"].items():
        L.append(f"**{key}**\n\n| symbol | signal | entry | stop | exit | reason | net R | segment |\n|---|---|---|---|---|---|---|---|")
        for r in rows:
            L.append(f"| {r.get('symbol', '')} | {r['signal_date']} | {r['entry']} | {r['stop']} | {r['exit_date']} | {r['reason']} | {r['r_net']:+.1f} | {r['segment']} |")
        L.append("")
    L.append("## Case studies (illustration only; not used to define anything)\n")
    for sym, rows in res["case_studies"].items():
        L.append(f"**{sym}**\n\n| set | exit | signal | entry | stop (risk %) | exit | reason | net R | MFE R | max in 250 bars | segment |\n|---|---|---|---|---|---|---|---|---|---|---|")
        for r in rows:
            L.append(f"| {r['set']} | {r['exit']} | {r['signal_date']} | {r['entry']} | {r['stop']} ({r['risk_pct']}%) | {r['exit_date']}{' (open)' if r['censored'] else ''} | {r['reason']} | "
                     f"{r['r_net']:+.2f} | {r['mfe_r']:.1f} | {r['mfe250_r'] if r['mfe250_r'] is not None else '–'} | {r['segment']} |")
        L.append("")
    return "\n".join(L)


if __name__ == "__main__":
    print(render(json.load(open(sys.argv[1], encoding="utf-8"))))

#!/usr/bin/env python3
"""Reads the collector's run log and prints, per run, the session it targeted, its start, exit status, verdict and observe outcome. Read-only; part of the hash-pinned
confirmation bundle. Usage: r13_runs.py <forward_collection_runs.log> <target-session>

Output (one per line):  TARGET_COUNT=<n>   FIRST=<start>|<exit>|<verdict>|<observe>   LATER=<start>|<exit>|<verdict>|<observe>   OTHER=<session>|<start>|<exit>|<verdict>
The log is a sequence of runs:  '<ts> forward-collection starting ...', the collector's one-line JSON report, '<ts> forward-collection finished: exit N'."""
import json
import re
import sys


def parse(text):
    runs, cur = [], None
    for line in text.splitlines():
        m = re.match(r"^(\S+) forward-collection starting", line)
        if m:
            cur = {"start": m.group(1), "exit": None, "session": None, "verdict": None, "observe": None}
            runs.append(cur)
            continue
        if cur is not None and line.startswith('{"applied"'):
            try:
                d = json.loads(line)
                cur["session"], cur["verdict"] = d.get("session"), d.get("verdict")
                cur["observe"] = next((s["outcome"] for s in d.get("steps", []) if s["name"] == "observe"), None)
            except ValueError:
                pass
            continue
        m = re.match(r"^(\S+) forward-collection finished: exit (\d+)", line)
        if m and cur is not None:
            cur["exit"] = int(m.group(2))
            cur = None
    return runs


def main(argv):
    try:
        text = open(argv[1], encoding="utf-8", errors="replace").read()
    except OSError:
        text = ""
    target = argv[2]
    runs = parse(text)
    mine = [r for r in runs if r["session"] == target]
    print(f"TARGET_COUNT={len(mine)}")
    for i, r in enumerate(mine):
        print(f"{'FIRST' if i == 0 else 'LATER'}={r['start']}|{r['exit']}|{r['verdict']}|{r['observe']}")
    for r in runs:
        if r["session"] != target:
            print(f"OTHER={r['session']}|{r['start']}|{r['exit']}|{r['verdict']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

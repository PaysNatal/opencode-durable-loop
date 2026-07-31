#!/usr/bin/env python3
"""Consolidate opencode sessions left behind by loop runs.

A loop run creates one opencode session PER AGENT (conductor / planner / worker /
validators) plus reformat calls — many fragments for a single task. They are flat
top-level sessions (no parent_id), so a task shows up as several "conversations".

This script groups sessions by (directory + time gap) and, for groups that contain
a loop-internal fragment, keeps ONE main session (the worker/task session) and
deletes the internal fragments and exact-title duplicates.

Safety: a session is only ever deleted if it matches a loop-internal pattern, is a
planner fragment (and a non-planner main exists to keep), or is an exact-title
duplicate inside a loop group. Anything else (e.g. a real user session that happens
to sit near a loop run) is NEVER deleted.

Usage:
  consolidate-sessions.py                # dry run — show the plan
  consolidate-sessions.py --apply        # actually delete
  consolidate-sessions.py --dedup-titles # also collapse identical titles globally
"""

import os
import sqlite3
import subprocess
import sys

DB = os.path.expanduser("~/.local/share/opencode/opencode.db")
GAP_MIN = 30  # start a new group when the gap between consecutive sessions exceeds this

# Titles that mark a session as a loop-INTERNAL fragment (never a real user session).
# Updated for zeroshot >= 6.12 agent roles (orchestrator, coordinator) and
# reformat session titles (Text-to-JSON / Text to JSON).
INTERNAL = [
    "conductor",
    "classification",
    "validator prompt",
    "validation prompt",
    "text-to-json",
    "text to json",
    "orchestrator",
    "coordinator",
]


def low(t):
    return (t or "").lower()


def is_internal(t):
    lt = low(t)
    return any(p in lt for p in INTERNAL)


def is_planner(t):
    # English "plan" only (loop planner titles end with " plan"); avoids matching
    # Chinese user planning sessions ("...规划").
    lt = low(t).strip()
    return lt.endswith(" plan") or " plan " in lt or lt.endswith(" planning")


def group_sessions(rows):
    groups, cur = [], None
    for sid, t, d, tc, tok in rows:
        if cur is None or d != cur["dir"] or (tc - cur["last"]) > GAP_MIN * 60000:
            cur = {"dir": d, "sess": [], "last": tc}
            groups.append(cur)
        cur["sess"].append({"id": sid, "title": t, "tc": tc, "tok": tok})
        cur["last"] = tc
    return groups


def main():
    apply = "--apply" in sys.argv
    dedup_titles = "--dedup-titles" in sys.argv

    con = sqlite3.connect(DB)
    rows = con.execute(
        "SELECT id, title, directory, time_created, COALESCE(tokens_output,0) "
        "FROM session ORDER BY directory, time_created"
    ).fetchall()

    delete = {}  # id -> (title, reason)

    # ── Phase 1: per-group consolidation of loop fragments ──
    for g in group_sessions(rows):
        sess = g["sess"]
        if len(sess) < 2:
            continue
        if not any(is_internal(s["title"]) for s in sess):
            continue  # not a loop group → leave untouched
        non_internal = [s for s in sess if not is_internal(s["title"])]
        pool = non_internal or sess
        main = sorted(pool, key=lambda s: (s["tok"], s["tc"]))[
            -1
        ]  # most substantial, then latest
        for s in sess:
            if s["id"] == main["id"]:
                continue
            t = s["title"]
            if is_internal(t):
                delete[s["id"]] = (t, "internal fragment")
            elif is_planner(t) and non_internal:
                delete[s["id"]] = (t, "planner fragment")
            elif any(o["id"] != s["id"] and o["title"] == t for o in sess):
                delete[s["id"]] = (t, "duplicate title in group")
            # else: keep (protects unique/non-internal sessions, e.g. real user sessions)

    # ── Phase 2 (opt-in): global exact-title dedup (keep latest per title) ──
    if dedup_titles:
        by_title = {}
        for sid, t, d, tc, tok in rows:
            if not t or sid in delete:
                continue
            by_title.setdefault(t, []).append({"id": sid, "tc": tc, "tok": tok})
        for t, lst in by_title.items():
            if len(lst) < 2:
                continue
            keep = sorted(lst, key=lambda s: (s["tok"], s["tc"]))[-1]
            for s in lst:
                if s["id"] != keep["id"]:
                    delete.setdefault(s["id"], (t, "global duplicate title"))

    print(f"Consolidation plan — {'APPLY' if apply else 'DRY RUN'}")
    print(f"Sessions to delete: {len(delete)}\n")
    for sid, (t, reason) in sorted(delete.items(), key=lambda x: (x[1][1], x[1][0])):
        print(f"  [{reason:24}] {sid[:18]}  {(t or '?')[:50]}")
    if not delete:
        print("  (nothing to delete)")

    if apply and delete:
        for sid in delete:
            subprocess.run(
                ["opencode", "session", "delete", sid],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        con.execute("VACUUM")
        con.commit()
        print(f"\n✓ Deleted {len(delete)} session(s) and vacuumed.")
    elif apply:
        print("\n✓ Nothing to delete.")


if __name__ == "__main__":
    main()

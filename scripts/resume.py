#!/usr/bin/env python3
"""Rehydrate working context after a compaction or fresh start.

Prints _STATE.md (the saved execution state — goal, plan, pinned set, in-flight
delegations, next action) followed by the INDEX summary lines for the pinned
pages. Together these reconstitute the working context: run this FIRST after any
context switch, before doing anything else.

Example:
  resume.py --store /path/to/pages
"""
import argparse
import os
import re


def read(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return None


def parse_pinned(state_text):
    """Pull the pinned: [a, b, c] list out of the _STATE frontmatter."""
    if not state_text:
        return []
    m = re.search(r"^pinned:\s*\[(.*?)\]", state_text, re.MULTILINE)
    if not m:
        return []
    return [x.strip() for x in m.group(1).split(",") if x.strip()]


def index_lines_for(store, addrs):
    index_text = read(os.path.join(store, "INDEX.md"))
    if not index_text:
        return []
    wanted = set(addrs)
    out = []
    for line in index_text.splitlines():
        if not line.startswith("- "):
            continue
        addr = line[2:].split("|", 1)[0].strip()
        if addr in wanted:
            out.append(line)
    return out


def main():
    ap = argparse.ArgumentParser(description="Rehydrate working context from the store.")
    ap.add_argument("--store", required=True, help="Path to the pages/ store directory.")
    args = ap.parse_args()

    state = read(os.path.join(args.store, "_STATE.md"))
    print("=" * 60)
    print("EXECUTION STATE (_STATE.md)")
    print("=" * 60)
    if state:
        print(state.rstrip())
    else:
        print("(no _STATE.md yet — this is a fresh task; write one before delegating)")

    pinned = parse_pinned(state)
    print()
    print("=" * 60)
    print(f"PINNED PAGES ({len(pinned)})")
    print("=" * 60)
    if pinned:
        lines = index_lines_for(args.store, pinned)
        for l in lines:
            print(l)
        missing = set(pinned) - {l[2:].split("|", 1)[0].strip() for l in lines}
        for m in sorted(missing):
            print(f"- {m} | (NOT IN INDEX — page may be missing or unwritten)")
    else:
        print("(none pinned)")


if __name__ == "__main__":
    main()

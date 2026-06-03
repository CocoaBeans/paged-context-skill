#!/usr/bin/env python3
"""Check whether a code-derived page is still fresh, or has gone stale.

Reads the page's frontmatter `sources` (path/span/rev) and recomputes the
content hash of each source span, comparing against the recorded rev. Exits 0 if
all sources match (fresh), 1 if any differ or are missing (stale — re-derive
before trusting the page). Pages with no sources are treated as immutable
(fresh).

Example:
  freshness.py --store /path/to/pages --addr findings/auth/legacyauth-LoginVC-142
"""
import argparse
import hashlib
import os
import re
import sys


def file_span_hash(store, path, span):
    candidates = [path]
    if not os.path.isabs(path):
        base = os.path.dirname(os.path.dirname(os.path.abspath(store)))
        candidates.append(os.path.join(base, path))
        candidates.append(os.path.join(os.getcwd(), path))
    for cand in candidates:
        try:
            with open(cand, "r", encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()
            break
        except OSError:
            continue
    else:
        return None
    if span:
        try:
            if "-" in span:
                a, b = span.split("-", 1)
                lo, hi = int(a), int(b)
            else:
                lo = hi = int(span)
            chunk = "".join(lines[lo - 1:hi])
        except (ValueError, IndexError):
            chunk = "".join(lines)
    else:
        chunk = "".join(lines)
    return hashlib.sha256(chunk.encode("utf-8")).hexdigest()[:16]


def parse_sources(front):
    """Parse the indented sources: list out of the frontmatter block."""
    sources = []
    in_sources = False
    cur = None
    for line in front.splitlines():
        if re.match(r"^sources:\s*$", line):
            in_sources = True
            continue
        if in_sources:
            if re.match(r"^\S", line):  # dedented — sources block ended
                break
            m = re.match(r"\s*-\s*path:\s*(.+)$", line)
            if m:
                if cur:
                    sources.append(cur)
                cur = {"path": m.group(1).strip()}
                continue
            m = re.match(r"\s*span:\s*(.+)$", line)
            if m and cur:
                cur["span"] = m.group(1).strip()
                continue
            m = re.match(r"\s*rev:\s*(.+)$", line)
            if m and cur:
                cur["rev"] = m.group(1).strip()
    if cur:
        sources.append(cur)
    return sources


def main():
    ap = argparse.ArgumentParser(description="Check a derived page's freshness.")
    ap.add_argument("--store", required=True, help="Path to the pages/ store directory.")
    ap.add_argument("--addr", required=True, help="Page address/slug to check.")
    args = ap.parse_args()

    page_path = os.path.join(args.store, args.addr + ".md")
    try:
        with open(page_path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        print(f"STALE: page {args.addr} not found")
        sys.exit(1)

    m = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
    front = m.group(1) if m else ""
    sources = parse_sources(front)

    if not sources:
        print(f"FRESH: {args.addr} has no sources (immutable)")
        sys.exit(0)

    stale = False
    for src in sources:
        recorded = src.get("rev")
        current = file_span_hash(args.store, src["path"], src.get("span"))
        loc = src["path"] + (f":{src['span']}" if src.get("span") else "")
        if current is None:
            print(f"STALE: source {loc} unreadable")
            stale = True
        elif not recorded:
            print(f"UNKNOWN: {loc} has no recorded rev (current {current})")
            stale = True
        elif current != recorded:
            print(f"STALE: {loc} changed (was {recorded}, now {current})")
            stale = True
        else:
            print(f"ok: {loc} ({current})")

    sys.exit(1 if stale else 0)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Write a page and/or upsert its INDEX line.

A "page" is one coherent unit of the paged-context store. By default this writes
the page file with YAML-ish frontmatter (summary, tags, validators from sources)
AND upserts the one-line entry for it in INDEX.md — the supervisor's single-
writer commit primitive.

Two flags split those responsibilities so a large fan-out can avoid funnelling
every worker's body back through the supervisor's context:

  --no-index    Write the page body ONLY; do not touch INDEX. A worker may call
                this to write its OWN page within its OWN disjoint namespace —
                page bodies in disjoint namespaces never collide, so this is
                coherence-safe. The worker then returns just (addr, summary,
                tags); it never touches INDEX.
  --index-only  Upsert the INDEX line ONLY; do not write a body. The supervisor
                calls this once per worker-written page to fold it into the page
                table. INDEX has exactly one writer (the supervisor), preserving
                coherence. Token count is read from the existing page file (or
                pass --tokens).

Default (neither flag): write body + upsert INDEX in one step (self-paging, or
small fan-outs where funnelling bodies is fine).

Body text is read from --body-file, or from stdin if not given.

Examples:
  # supervisor self-page (default): write body + index together
  echo "## finding ..." | page_write.py --store $S --addr findings/x \
      --summary "..." --tags a,b --sources '[{"path":"src/x.swift","span":"42"}]'

  # worker writes its own body (disjoint namespace), no INDEX touch
  echo "## digest ..." | page_write.py --store $S --addr arch/foo --no-index \
      --summary "..." --tags subsystem --sources '[{"path":"Foo/Foo.swift"}]'

  # supervisor folds that worker page into the page table
  page_write.py --store $S --addr arch/foo --index-only \
      --summary "..." --tags subsystem
"""
import argparse
import hashlib
import json
import os
import sys


def file_span_hash(store, src):
    """Compute a content hash for a source (whole file, or a line span).

    Paths are resolved relative to the repo if not absolute; we try the store's
    grandparent (project root) and the cwd as bases. Returns None if unreadable,
    so callers can record the source without a validator rather than failing.
    """
    path = src.get("path")
    if not path:
        return None
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

    span = src.get("span")
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


def render_frontmatter(summary, tags, sources, store):
    lines = ["---", f"summary: {summary}"]
    if tags:
        lines.append("tags: [" + ", ".join(tags) + "]")
    if sources:
        lines.append("sources:")
        for src in sources:
            rev = src.get("rev") or file_span_hash(store, src)
            lines.append(f"  - path: {src.get('path','')}")
            if src.get("span"):
                lines.append(f"    span: {src['span']}")
            if rev:
                lines.append(f"    rev: {rev}")
    lines.append("---")
    return "\n".join(lines)


def body_token_estimate(page_path):
    """Estimate tokens from an existing page file's body (after frontmatter)."""
    try:
        with open(page_path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return None
    if text.startswith("---"):
        parts = text.split("---", 2)
        body = parts[2] if len(parts) == 3 else text
    else:
        body = text
    return max(1, len(body) // 4)


def upsert_index(store, addr, tags, summary, tokens):
    index_path = os.path.join(store, "INDEX.md")
    tagstr = ",".join(tags) if tags else "-"
    new_line = f"- {addr} | {tagstr} | ~{tokens}tok | {summary}"
    header = "# Page table (INDEX)\n\n"
    lines = []
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as fh:
            content = fh.read()
        body_lines = [l for l in content.splitlines()
                      if l.strip() and not l.startswith("#")]
        replaced = False
        for l in body_lines:
            if l.startswith(f"- {addr} |"):
                lines.append(new_line)
                replaced = True
            else:
                lines.append(l)
        if not replaced:
            lines.append(new_line)
    else:
        lines.append(new_line)

    tmp = index_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(header + "\n".join(sorted(set(lines))) + "\n")
    os.replace(tmp, index_path)


def main():
    ap = argparse.ArgumentParser(description="Write a page and/or upsert its INDEX line.")
    ap.add_argument("--store", required=True, help="Path to the pages/ store directory.")
    ap.add_argument("--addr", required=True, help="Page address/slug (may contain / for namespacing).")
    ap.add_argument("--summary", default="", help="One-line summary; becomes the INDEX entry.")
    ap.add_argument("--tags", default="", help="Comma-separated tags.")
    ap.add_argument("--sources", default="", help="JSON array of {path,span?,rev?} for validators.")
    ap.add_argument("--body-file", default="", help="File to read body from (default: stdin).")
    ap.add_argument("--no-index", action="store_true",
                    help="Write page body only; skip INDEX (worker writing its own namespace).")
    ap.add_argument("--index-only", action="store_true",
                    help="Upsert INDEX line only; skip writing a body (supervisor batch-indexing).")
    ap.add_argument("--tokens", type=int, default=0,
                    help="Token estimate for --index-only when the page body isn't readable.")
    args = ap.parse_args()

    if args.no_index and args.index_only:
        ap.error("--no-index and --index-only are mutually exclusive")

    os.makedirs(args.store, exist_ok=True)
    tags = [t.strip() for t in args.tags.split(",") if t.strip()]
    page_path = os.path.join(args.store, args.addr + ".md")

    # --- index-only: just fold an already-written page into the page table ---
    if args.index_only:
        if not args.summary:
            ap.error("--index-only requires --summary")
        tokens = args.tokens or body_token_estimate(page_path) or 1
        upsert_index(args.store, args.addr, tags, args.summary, tokens)
        print(f"indexed {args.addr} (~{tokens}tok)")
        return

    # --- write the page body ---
    sources = json.loads(args.sources) if args.sources.strip() else []
    if args.body_file:
        with open(args.body_file, "r", encoding="utf-8") as fh:
            body = fh.read()
    else:
        body = sys.stdin.read()

    os.makedirs(os.path.dirname(page_path), exist_ok=True)
    front = render_frontmatter(args.summary, tags, sources, args.store)
    tmp = page_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(front + "\n\n" + body.rstrip("\n") + "\n")
    os.replace(tmp, page_path)
    tokens = max(1, len(body) // 4)

    if args.no_index:
        print(f"wrote page {args.addr} (~{tokens}tok), INDEX untouched")
    else:
        upsert_index(args.store, args.addr, tags, args.summary, tokens)
        print(f"wrote page {args.addr} (~{tokens}tok) and updated INDEX")


if __name__ == "__main__":
    main()

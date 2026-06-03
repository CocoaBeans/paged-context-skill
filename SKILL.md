---
name: paged-context
description: >-
  Virtual-memory discipline for the context window. Treat the on-disk memory
  store as swap and supervising subagents as DMA workers, so a single task can
  process far more material than fits in context at once. Use this whenever a
  task's material clearly exceeds what one context can hold or risks
  mid-task compaction — large multi-file audits or refactors, repo-wide
  searches, long multi-source research, migrations, or any job where you catch
  yourself thinking "this won't all fit." Also use it the moment you notice
  context filling up and want work to survive compaction. Don't wait to be
  asked by name; reach for it when the shape of the task is "more than fits."
---

# Paged Context

Virtual memory for the context window. The context is fast, scarce "RAM"; the
on-disk memory store is vast, slower "swap"; supervising subagents are
co-processors that chew through disk-resident spans and hand back compact
results. With this discipline a task's *addressable* material is effectively
unlimited even though the *working set* stays bounded — exactly like virtual
memory gives a process a huge address space on top of small physical RAM.

This skill is a discipline, not a feature. It works by you following the
protocol below and using the three helper scripts. It does not change the
harness; it changes how you spend context so that nothing important ever lives
*only* in volatile context.

## The store

Pages live under the active project's memory directory in a `pages/`
subdirectory — referred to below as `$STORE`. Find the project memory dir
(the one holding `MEMORY.md`) and use `<that-dir>/pages` as `$STORE`. Create
it if missing. Three special files anchor everything:

- `$STORE/INDEX.md` — the **page table**. One line per page. This is the only
  catalog you keep in context; it is itself a page on disk, so it survives
  compaction and can be re-read at any time.
- `$STORE/_STATE.md` — the **register file**. Your saved execution state:
  goal, plan with checkmarks, what's pinned, what's in flight, what's next.
- `$STORE/<addr>.md` — **data pages**. One coherent unit each.

## Core invariant: write-through

The instant you produce a novel, full-fidelity artifact — a file digest, a
decision, a sub-result, a research finding — persist it to a page *before*
continuing. Then keep only a one-line summary plus the address inline.

Why this matters: the harness may compact context on its own, at a moment you
don't choose. If the only copy of something lives in context, compaction can
silently destroy it. Write-through means context holds the *page table and the
hot set*, never the sole copy of anything. A "page fault" re-reads full
fidelity from disk. This is what converts lossy swap into effectively lossless
memory — at the cost of more writes, which is a cost worth paying.

Use `scripts/page_write.py` to write a page and update INDEX atomically in one
step (this also keeps you as the single writer — see Coherence).

## Surviving a context switch (the register file)

Write-through protects your *data*. `_STATE.md` protects your *execution
state* — the equivalent of saving registers and the stack pointer before an OS
context switch, so a process resumes exactly where it left off.

```markdown
---
goal: <the task in one line>
plan:
  - [x] step done → page <addr>
  - [ ] step pending
pinned: [addr-a, addr-b]        # current working set — never evict
in-flight:                       # delegations dispatched but not committed
  - worker: <label>
    contract: <one-line summary of what it must return>
    namespace: <assigned output prefix>
last-action: <what you just did>
next-action: <what to do on resume>
open-questions: [...]
---
```

**Recovery protocol — do this first after any compaction, before anything
else:** run `scripts/resume.py --store $STORE`. It prints `_STATE.md` plus the
INDEX summaries for the pinned pages. Those two things fully reconstitute your
working context. The only thing that must survive in volatile context is the
path to the store, and that's re-derivable (find `MEMORY.md`), so recovery is
total.

**Checkpoint cadence:** rewrite `_STATE.md` at every plan-step boundary and
again immediately *before* any delegation (recording the in-flight marker).
That's frequent enough to bound loss to a single step, without the noise of
rewriting every turn. Checkpoints only touch `_STATE.md` (small); data pages
are already durable via write-through, so this stays cheap.

## Pages

A page is **one coherent retrievable unit** within a soft size band — roughly
500–1500 tokens. Not smaller (a page per trivial atom bloats the page table
and costs a fault per fact — fragmentation), not larger (it won't fit cleanly
and defeats selective paging).

- **Oversize units split recursively.** A huge file's digest that exceeds the
  band becomes an *index page* whose body links per-section child pages via
  `[[slug]]`. Same recursion workers use when their output overflows.
- **Cross-cutting facts get their own page — never duplicate.** Knowledge that
  spans many files (e.g. "the auth flow touches these six files") goes in a
  dedicated relation page and is referenced by `[[link]]`, not copied into each
  file's page. Copying creates a coherence bug: you update one copy and the
  others rot. This is just normalization.
- **Deterministic addresses.** Derive each page's slug from stable identity —
  a file path, or `hash(file:line:rule)` for a finding — so that re-running the
  same work *overwrites* the same address instead of spawning duplicates.
- **Hugepages for broad reasoning.** You write fine-grained, but you often
  reason across many pages at once. If every reasoning step faults in N pages,
  you thrash. So alongside fine pages, mint occasional coarse "section summary"
  pages — the gist of a whole subsystem — for the broad passes. Fine pages for
  "I need this one fact"; hugepages for "I need the shape of the whole thing."

## Validators (cache invalidation)

A page derived from code goes stale when the code changes. Record a validator
in each derived page's frontmatter — the source path and a content hash (or git
rev) of the span it came from. `page_write.py` does this for you from the
`sources` you pass.

Before trusting a code-derived page on page-in, check it with
`scripts/freshness.py --store $STORE --addr <addr>`. Stale → re-derive from the
real source rather than reasoning on rot. Pure-reasoning pages (decisions,
research syntheses) are immutable once written and need no validator.

## Eviction and thrash

You can't measure your own context size precisely, so eviction is *advisory* —
an optimization to delay compaction, not a correctness mechanism (write-through
+ `_STATE` are what guarantee correctness). Heuristics:

- **Pin** the current working set in `_STATE.pinned`; never evict those.
- When context feels heavy, drop the full text of the least-recently-touched
  pages from your inline reasoning — their summaries stay in INDEX, and you can
  fault them back anytime.
- **Thrash signal:** if you fault the same address three+ times in one task,
  the sub-task is too big for the working set. Stop swapping — split it and
  delegate (below).

## Delegation: subagents as DMA workers

Paging keeps *your* memory small. Delegation lets *total work processed* exceed
any single context: a subagent has its own fresh context, so it can read a huge
span, burn its own tokens, and return a compact page while yours stays small.

This is also how you handle the one thing paging can't fix — a conclusion that
needs more attended-to at once than fits. There's no swapping out of that; you
must decompose. So: **workers are the `map`, you are the `reduce`.** Fan out
workers over disjoint slices, collect their pages, then reduce (paging the
result pages in, or delegating the reduce too if even that won't fit).

When to delegate vs. self-page: self-page for working-set-sized material;
delegate when a span is too big to page through yourself, or when you need many
slices processed independently.

**The worker contract is precise and matters** — workers start cold and have no
write authority. Read `references/worker-contract.md` before dispatching;
it has the prose template, the structured-output schema to pass as the agent's
`schema`, a filled example, and the commit/fault-servicing steps. The essentials:

- A worker is a **pure function** `read-set → proposed pages`. It returns data;
  *you* commit it. No worker writes the store.
- Each worker gets a **disjoint output namespace**, so their results can never
  collide and your commit is conflict-free by construction.
- Output comes back in a **commit-ready schema** (slug, summary, tags, body,
  sources) that maps 1:1 onto pages — no reformatting.
- If a worker's read-set is insufficient, it returns a **fault** (`{reason,
  need}`) instead of guessing — a syscall back to you. You service it by
  widening the read-set and re-dispatching.

## Coherence

The only file that strictly needs a **single writer** is `INDEX.md` — that's
the shared mutable structure two writers could clobber. **You** own INDEX.
Record each dispatched-but-uncommitted worker in `_STATE.in-flight` so an
interrupted delegation is recoverable rather than silently lost.

Page **bodies** are different: a body written into a worker's *own disjoint
namespace* can't collide with anything. So there are two delegation modes,
and the right one depends on fan-out size:

- **Small fan-out — workers propose, you commit.** Workers return their digest
  as data; you write it with `page_write.py` (body + INDEX together). Simplest,
  but every body funnels back through *your* context.
- **Large fan-out — workers write bodies, you own the index.** Each worker
  writes its own page with `page_write.py --no-index` (body only, in its
  disjoint namespace) and returns just `(addr, summary, tags)` — a single line.
  You then fold each into the page table with `page_write.py --index-only`.
  This removes the body funnel: full bodies go straight to disk, only metadata
  crosses back to you, and INDEX still has exactly one writer. Reach for this
  when bodies are large or numerous enough that funnelling them would bloat your
  context. (Workers writing bodies is the one sanctioned exception to "workers
  don't write" — bounded strictly to their own namespace, never to INDEX.)

## Scaling the page table

A flat `INDEX.md` comfortably handles hundreds of pages (each line is a handful
of tokens). Only when INDEX itself grows too large to keep in context do you
need a two-level page table: split INDEX into topic sub-indexes and make
`INDEX.md` an index of those. Don't build this preemptively — fault it in only
when you actually hit the wall, the same demand-paging philosophy applied to
your own machinery.

## Worked example (repo-wide deprecated-API audit)

1. Write `_STATE`: goal, plan = [scan N dirs, reduce, report].
2. **Map:** fan out one worker per dir (disjoint namespaces `findings/<dir>/`),
   each returning findings pages capped to fit. Record them in `_STATE.in-flight`
   before dispatch.
3. As each returns, commit its pages with `page_write.py`, check off the plan
   step, clear its in-flight marker. If compaction hits after dir 5 → `resume.py`
   shows "dirs 1–5 done (pages…), 6–8 pending." Zero loss.
4. **Reduce:** page in the findings pages (or delegate the reduce to a worker
   that reads them from disk if they won't all fit), write a `report` page.
5. Working set never exceeded a handful of summaries plus one page at a time;
   total material processed = the whole repo.

## Scripts

- `scripts/page_write.py --store $STORE --addr <slug> --summary <s> [--tags a,b]
  [--sources '<json>'] [--body-file PATH | pipe body on stdin]`
  Writes a page with frontmatter (validators from `sources`) and upserts its
  INDEX line. Your commit primitive. Two flags split body-write from
  index-write (see Coherence): `--no-index` writes the body only (a worker
  writing its own namespace); `--index-only` upserts the INDEX line only (you
  folding a worker-written page into the page table).
- `scripts/resume.py --store $STORE` — prints `_STATE.md` and the INDEX
  summaries for pinned pages. Run first after any compaction.
- `scripts/freshness.py --store $STORE --addr <slug>` — checks a derived page's
  validators against current sources; exits non-zero if stale.

Run any with `-h` for full usage.

**Shell note (zsh):** when committing several pages in one shell, wrap the call
in a function, not a variable — `PW="python3 …"; $PW --addr …` breaks because
zsh does not word-split unquoted parameter expansions, so the whole string is
treated as one command name. Use:

```sh
S=<store>
pw() { python3 <skill>/scripts/page_write.py --store "$S" "$@"; }
pw --addr arch/foo --summary "…" --tags a,b <<'PAGE_EOF'
…body…
PAGE_EOF
```

Use a quoted heredoc delimiter (`<<'PAGE_EOF'`) so backticks and `$` in the
body are not expanded.

# Worker Contract

How to dispatch a supervising subagent ("worker") so it processes a large span
and returns commit-ready pages, without ever writing the store itself.

## Governing principle

A worker is a **pure function**: `read-set → proposed pages`. In the default
mode it has zero side effects and the supervisor (you) applies all effects.
There are two delegation modes (pick by fan-out size; see SKILL.md → Coherence):

- **Propose mode (default, small fan-out):** the worker returns its pages as
  data; you write body + INDEX with `page_write.py`. Simplest; every body
  funnels back through your context.
- **Worker-writes-body mode (large fan-out):** the worker writes its OWN page
  bodies with `page_write.py --no-index`, strictly within its assigned disjoint
  namespace, and returns only `(addr, summary, tags)` per page. You fold each
  into the page table with `page_write.py --index-only`. Bodies never cross
  back through your context; INDEX still has exactly one writer (you). This is
  the one sanctioned exception to "no writes" — bounded to the worker's own
  namespace, never to INDEX.

The rest of this doc describes propose mode; the worker-writes-body variant
differs only in who calls `page_write.py --no-index` for the body and what the
worker returns (metadata lines instead of full bodies).

Workers start with a **cold, fresh context**. They know nothing about
`_STATE`, the page conventions, or the task history. The contract must carry
everything they need. You teach a worker a *tiny output schema*, not the whole
skill.

## Five properties to preserve

1. **Self-contained** — the prompt carries the full read-set, task, output
   shape, and addressing rule.
2. **Partitioned address space** — each worker gets a *disjoint* output
   namespace, so two workers physically cannot collide and your commit is
   conflict-free.
3. **Commit-ready output** — structured data mapping 1:1 onto pages; no prose
   parsing, no reformatting on your end.
4. **Fault-back, don't wander** — if the read-set is insufficient, the worker
   returns a structured fault rather than spelunking the repo (which breaks
   bounded cost) or hallucinating.
5. **Self-paging overflow** — if output would exceed the cap, the worker splits
   into children plus a child-index page.

## Prose contract (goes in the agent prompt)

```markdown
ROLE
You are a worker. Produce data only — you have NO authority to write
files or modify any store. Return your result via the structured
output schema; the supervisor commits it.

READ SET (your entire address space — read ONLY these)
  <explicit list: file paths, globs, page addresses, line ranges>

TASK
  <the precise pure computation: what to extract / analyze / transform>

OUTPUT
  Return one page per coherent unit. For each page provide:
    - slug   : computed by the ADDRESSING RULE below (so re-runs dedup)
    - summary: one line, <=120 chars — becomes the index entry
    - tags   : retrieval tags
    - body   : full-fidelity markdown — the durable content
    - sources: every (path, span, rev/hash) you derived from
  If total output would exceed <CAP> tokens, DO NOT return one blob:
  split into child pages and also return an index_page whose body
  links the children via [[slug]].

ADDRESSING RULE
  namespace: <assigned prefix, disjoint per worker>
  slug = <deterministic formula, e.g. ruleid-<file>-<line>>

FAULT
  If the READ SET is insufficient, do NOT guess and do NOT read
  outside it. Return a fault: {reason, need:[...]}.
```

## Structured-output schema (pass as the agent's `schema`)

Passing this as the `schema` option forces the worker to return exactly this
shape (validated at the tool layer, so it retries on mismatch). `pages`
(success) and `fault` (blocked) are mutually exclusive — branch on which is
present.

```json
{
  "type": "object",
  "properties": {
    "pages": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "slug":    { "type": "string" },
          "summary": { "type": "string", "maxLength": 120 },
          "tags":    { "type": "array", "items": { "type": "string" } },
          "body":    { "type": "string" },
          "sources": {
            "type": "array",
            "items": {
              "type": "object",
              "properties": {
                "path": { "type": "string" },
                "span": { "type": "string" },
                "rev":  { "type": "string" }
              },
              "required": ["path"]
            }
          }
        },
        "required": ["slug", "summary", "body"]
      }
    },
    "index_page": {
      "type": ["object", "null"],
      "properties": {
        "slug":    { "type": "string" },
        "summary": { "type": "string" },
        "body":    { "type": "string" }
      }
    },
    "fault": {
      "type": ["object", "null"],
      "properties": {
        "reason": { "type": "string" },
        "need":   { "type": "array", "items": { "type": "string" } }
      }
    }
  }
}
```

## Filled example (deprecated-API audit, one worker)

Prompt:

```markdown
ROLE: worker, data only, no writes.

READ SET:
  src/auth/**/*.swift

TASK:
  Find every call site of the deprecated `legacyAuth(_:)` API. For each,
  capture file, line, the enclosing function, and a one-line note on the
  modern replacement.

OUTPUT: one page per call site.

ADDRESSING RULE:
  namespace: findings/auth/
  slug = legacyauth-<file-basename>-<line>

FAULT: if `legacyAuth` is defined outside the read set and you can't
  determine the replacement, fault with need:["definition of legacyAuth"].
```

Returned:

```json
{
  "pages": [{
    "slug": "legacyauth-LoginVC-142",
    "summary": "legacyAuth in LoginVC.signIn() — replace with AuthSession.start",
    "tags": ["deprecation", "auth"],
    "body": "## legacyAuth at LoginVC.swift:142\nIn `signIn()`...\nReplacement: `AuthSession.start(...)`",
    "sources": [{"path": "src/auth/LoginVC.swift", "span": "142", "rev": "abc123"}]
  }],
  "index_page": null,
  "fault": null
}
```

## Committing the result (the supervisor's half)

```
for each page in result.pages:
    addr = "<namespace>/<page.slug>"
    page_write.py --store $STORE --addr <addr> --summary <page.summary> \
        --tags <page.tags> --sources '<page.sources>'   # body on stdin
    # page_write upserts the INDEX line for you
if result.index_page: commit it the same way
check off the plan step in _STATE; clear this worker's in-flight marker

if result.fault:
    leave the step open; service the fault — widen the read-set per
    fault.need (or point at the right address) — and re-dispatch the
    same worker.
```

## Why two properties make this safe

- **Validators for free** — the worker reports `sources` with rev/hash;
  `page_write.py` stamps them into frontmatter, so later freshness checks are
  O(1) with zero extra worker effort.
- **Idempotent re-runs** — deterministic slugs mean a second pass overwrites the
  same addresses instead of duplicating. Dedup solved at the addressing layer.

## Sizing note

Keep each worker's read-set sized to fit one worker's context. If a slice is
too big for a single worker, that's the supervisor's scheduling job: split it
across more workers with disjoint namespaces. A worker that finds its own
read-set too large should fault rather than truncate silently.

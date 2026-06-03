# paged-context

A virtual-memory discipline for an AI agent's context window, packaged as a
[Claude Code](https://claude.com/claude-code) skill.

It treats the on-disk memory store as **swap** and supervising subagents as
**workers**, so a single task can process far more material than fits in
context at once — large multi-file audits or refactors, repo-wide searches,
long multi-source research, migrations — while the working set stays bounded.
Work is checkpointed to disk so it survives mid-task context compaction.

Under the hood: write-through pages, a `_STATE` register file for
context-switch recovery, source validators for cache invalidation, and a
map-reduce delegation model with single-writer coherence. See
[`SKILL.md`](SKILL.md) and [`references/worker-contract.md`](references/worker-contract.md).

## Prerequisites

Python 3 (standard library only — nothing to `pip install`).

## Install

### Option A — clone into your skills directory (recommended)

Updates then become a `git pull`:

```sh
git clone https://github.com/CocoaBeans/paged-context-skill.git ~/.claude/skills/paged-context
# later, to update:
git -C ~/.claude/skills/paged-context pull
```

### Option B — one-line installer

```sh
curl -fsSL https://raw.githubusercontent.com/CocoaBeans/paged-context-skill/main/install.sh | sh
```

### Option C — download the packaged skill

Grab `paged-context.skill` from the [Releases](https://github.com/CocoaBeans/paged-context-skill/releases)
page and unzip it into your skills directory:

```sh
unzip paged-context.skill -d ~/.claude/skills/
```

After any method, reload Claude Code — it auto-discovers skills in
`~/.claude/skills/`.

## Project-scoped install (share with a repo's whole team)

Instead of your personal `~/.claude/skills/`, drop the `paged-context/` folder
into a repository's `.claude/skills/` and commit it. Everyone using Claude Code
in that repo gets the skill automatically, no install step.

## Make it trigger proactively (optional)

Skills are consulted based on their description; to bias toward using it, add a
pointer to your `~/.claude/CLAUDE.md` (or a repo `CLAUDE.md`):

```markdown
When a task's material exceeds what one context window can hold, or risks
mid-task compaction — repo-wide audits/searches, multi-file refactors or
migrations, long multi-source research — use the paged-context skill rather
than trying to hold everything at once.
```

## License

MIT — see `LICENSE` if present, otherwise use freely.

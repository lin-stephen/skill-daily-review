# daily-review

A `/daily-review` skill for Claude Code: scans a day's local AI agent sessions,
git commits and Slack activity, interviews you about it in 繁體中文, and appends
a 做得好的 / 可以更好的 / 有感 / 感謝 entry to your Heptabase journal.

## Install

```bash
ln -s ~/projects/skill-daily-review ~/.claude/skills/daily-review
```

Edits to this repo take effect immediately — no reinstall.

## Configure

Settings live in `~/.claude/daily-review.local.md` — YAML frontmatter, the same
shape as the Claude Code plugin-settings convention, but at user level because a
daily review spans the whole machine rather than one project.

```bash
python3 scripts/config.py --init    # create it, detecting values from this machine
python3 scripts/config.py --show    # effective settings, and where each came from
```

| Key | Default | Meaning |
|---|---|---|
| `day_start` | `"05:00"` | local time a work day begins |
| `repo_roots` | `~/projects`, `~/src`, `~/code`, `~/work` | where to sweep for git repos |
| `repo_depth` | `6` | how deep under each root to look for `.git` |
| `git_authors` | *(empty)* | commit authors; empty falls back to your global git identity |
| `slack_user_id` | *(empty)* | empty reads your ID from the Slack tool description |
| `journal_lookback` | `7` | how many recent journal entries to read |
| `sources` | all three | which agent session stores to scan |

Edit it whenever you like — it is read at run time, so changes apply on the next
run. **No Claude Code restart**, because none of this is a hook. A command-line
flag still beats the file, and the file beats the defaults.

The file is outside this repo and is yours alone; nothing machine-specific is
committed here.

## Use

```
/daily-review                        # yesterday
/daily-review today
/daily-review 2026-11-10
/daily-review 2026-11-10..2026-11-12 # one combined entry, filed on 11-12
/daily-review last week
```

## What it does

0. Resolve the range (default yesterday). A **work day runs 05:00–05:00**, so
   work done after midnight counts toward the day it started on.
1. Scan Claude Code, Codex CLI and Antigravity session history, then git
   commits — session repos first, then a sweep of all repos under your working
   directories.
2. Scan Slack: your messages in channels and DMs, their threads, in-channel
   follow-ups, `@`-mentions, and emoji reactions others left on your messages.
3. Read the last ~7 days of Heptabase journals for your voice, and for the
   可以更好的 items you have not closed yet.
4. Rank the 7 most significant topics — by friction, by decisions, and by
   collaboration signal. Not by hours spent.
5. Interview you in 繁體中文, **asking what you remember before showing what it
   found**, so its summary does not overwrite your memory.
6. Draft in your voice, from what survived the interview — in the house
   format taken from your own journals, provenance tags and all.
7. Show the draft, and append it to that day's journal only after you say OK.

## Layout

```
SKILL.md                       the workflow
scripts/scan_agents.py         Claude Code + Codex + Antigravity -> digest
scripts/scan_git.py            authored commits across local repos
scripts/config.py              settings loader; --init / --show
references/slack-scan.md       Slack query recipe and scope
references/data-sources.md     storage formats, caps, known limitations
references/heptabase-journal.md CLI commands + the house journal format
```

The scripts are standalone — run them directly to inspect a day:

```bash
python3 scripts/scan_agents.py --from 2026-11-12 --to 2026-11-12
python3 scripts/scan_git.py    --from 2026-11-12 --to 2026-11-12

# plain calendar day instead of the 05:00 work day
python3 scripts/scan_agents.py --from 2026-11-12 --day-start 00:00
```

Both default to `--day-start 05:00` and print the exact window they used.

## Requirements

- Python 3.9+ (stdlib only)
- The Heptabase desktop app **open**, with its CLI enabled (Settings > AI
  Features). Verified against CLI 0.11.0. Without it step 7 cannot write, and
  the skill hands you the draft as text instead.
- The Slack MCP plugin, signed in.

## Known gaps

- **Antigravity** stores transcripts in `implicit/*.pb`, which are binary. Only
  a conversation title and preview are recoverable. The skill reports this as a
  gap rather than implying the tool went unused.
- Slack `after:` / `before:` exclude the named day — the recipe uses `on:` for
  single days for this reason.
- The journal format in `references/heptabase-journal.md` was reverse-engineered
  from entries dated 2026-10-28 .. 2026-11-06. Its worked example is synthetic,
  so the file is safe to share; the skill reads your actual recent entries at
  run time for the real voice. If the house style drifts, update that file.

## License

MIT — see [LICENSE](LICENSE). Use it, fork it, adapt it; just keep the
copyright notice.

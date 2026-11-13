# Local data sources

Verified against this machine on 2026-11-13. Formats follow those described in
<https://github.com/jhlee0409/claude-code-history-viewer>.

## Claude Code

`~/.claude/projects/<url-encoded-cwd>/<session-uuid>.jsonl`, one JSON object per
line.

- Real user input: `type:"user"` where `toolUseResult` is absent and the content
  is a string or `text` blocks. Entries carrying `tool_result` blocks are tool
  output wearing the user role — **not** something the user typed.
- `isSidechain: true` marks subagent turns. Their prompts come from the main
  agent, so they are excluded from user turns, but their errors still count as
  friction.
- Assistant content blocks are `text`, `thinking` and `tool_use`. File paths
  come from `tool_use.input.file_path` / `path` / `notebook_path`.
- Friction: `tool_result` blocks with `is_error: true`.
- Per-entry fields worth keeping: `timestamp` (UTC, `Z`), `cwd`, `gitBranch`,
  `sessionId`.

## Codex CLI

`~/.codex/sessions/YYYY/MM/DD/rollout-<local-ts>-<uuid>.jsonl`. The directory
and filename are **local** time; the `timestamp` field inside is UTC.

- `type:"session_meta"` → `payload.cwd`, `payload.id`.
- `type:"turn_context"` → `payload.cwd`, `payload.model`.
- `type:"event_msg"`, `payload.type:"item_completed"` → `payload.item.type` is
  one of `UserMessage`, `AgentMessage`, `CommandExecution`, `Reasoning`.
- `CommandExecution` carries `command`, `exit_code`, `aggregated_output` and
  `duration`. A non-zero `exit_code` is the friction signal.
- `type:"response_item"`, `payload.type:"message"` duplicates the same content
  in raw API form. The scanner uses it only as a fallback for older rollouts,
  and skips `<environment_context>` / `<user_instructions>` preamble.

## Antigravity CLI

`~/.gemini/antigravity-cli/`:

- `conversation_summaries.db` — SQLite. `conversation_summaries` holds
  `title`, `preview`, `step_count`, `workspace_uris`, `last_user_input_time`.
  `conversations_fts` is an FTS5 index with `user_body` / `agent_body`.
- `conversations/` — observed empty.
- `implicit/*.pb` — binary, no readable strings. Not parseable.

**Known limitations.** The database is WAL-mode and usually held open by the
CLI, so `mode=ro` fails with "unable to open database file"; the scanner
snapshots the `.db` plus its `-wal` / `-shm` sidecars to a temp dir and reads
the copy. Reading the main file without the sidecars also under-reports rows.
`conversations_fts` was empty when checked, so in practice Antigravity yields
only a title and a preview per conversation — enough to name a topic, not
enough to analyse one. Report that as a gap rather than working around it.

## Git

Commits are found by sweeping for `.git` directories to `repo_depth` (default
6) under the configured `repo_roots`, matching `--author` against the configured
`git_authors` — or, when those are empty, the global `user.email` and
`user.name`. `--all` covers every ref, `--no-merges` keeps the list to authored
work. A sweep of ~87 repos takes under 3 seconds.

Depth matters more than it looks: dropping from 6 to 4 on this machine cuts
the sweep from 87 repos to 62, because repos nested as
`<root>/<group>/<subgroup>/<repo>` sit at depth 4 already. If the git section
comes back thinner than expected, raise `repo_depth` before suspecting the
author patterns.

Both roots and authors come from `~/.claude/daily-review.local.md`; see
"Settings" below. The built-in fallbacks (`~/projects`, `~/src`, `~/code`,
`~/work`) exist only so the script runs before that file is created.

Repos passed via `--priority` — the cwds from that day's agent sessions — are
reported first and labelled, so the agenda keeps its connection to the sessions.
The sweep then catches commits made without an agent, which is often where the
day's real output hides.

## Caps applied by `scan_agents.py`

Tuned so a normal day fits in context while friction stays visible.

| What | Cap |
|---|---|
| User turn | 1500 chars |
| Agent reply | 350 chars, 25 per session |
| Error excerpt | 220 chars, 20 per session |
| Files touched | 40 per session |
| Bash commands kept for repeat detection | 25 per session |
| Sessions | 60 |

A command repeated 3+ times in one session is flagged as possible friction.

If a day overflows, narrow with `--source` and run the sources separately
rather than raising the caps.

## Timezone and the work-day boundary

Both scripts work in the machine's local zone (Asia/Taipei, CST+0800). Stored
timestamps are UTC, so entries stamped `2026-11-12T16:58Z` belong to local
**2026-11-13 00:58**. (Codex *filenames* are already local, which makes this
easy to get backwards.)

`--from` / `--to` name **work days**, not calendar days. A work day runs from
`--day-start` (default `05:00`) to the same time the next day, so the Nov 12
work day is `2026-11-12 05:00 → 2026-11-13 05:00`. Late-night work then stays
with the day it started on instead of being filed under the next morning.

Consequences worth remembering:

- Before 05:00, the *current* work day is still yesterday's date. At 02:00 on
  Nov 13, "yesterday" resolves to **Nov 11**. `current_work_day()` in both
  scripts handles this; do not recompute it by hand.
- `git log --since/--until` is inclusive at both ends, so `scan_git.py` stops
  at `04:59:59` rather than `05:00:00`.
- Commit lines print `HH:MM` for the first day of the range and the full
  `YYYY-MM-DD HH:MM` for anything after midnight, so a 01:30 commit cannot be
  mistaken for 01:30 the previous morning.
- Pass `--day-start 00:00` to both scripts for plain calendar days, or set
  `day_start: "00:00"` in the settings file to make it permanent.

Measured effect on 2026-11-12: the 05:00 boundary picked up **9 agent sessions
instead of 8**, recovering a 01:19–01:36 Elasticsearch/Grafana debugging session
on Nov 13 that belonged to Nov 12's work.


## Settings

`~/.claude/daily-review.local.md` holds the user's own values: YAML
frontmatter, the same shape the Claude Code plugin-settings convention uses,
but at user level because a daily review spans the whole machine rather than
one project.

```bash
python3 scripts/config.py --init    # write it, with values detected here
python3 scripts/config.py --show    # effective settings and where each came from
```

| Key | Default | Meaning |
|---|---|---|
| `day_start` | `"05:00"` | local time a work day begins |
| `repo_roots` | `~/projects`, `~/src`, `~/code`, `~/work` | where to sweep for git repos |
| `repo_depth` | `6` | how deep under each root to look for `.git` |
| `git_authors` | *(empty)* | commit authors; empty falls back to global git identity |
| `slack_user_id` | *(empty)* | empty reads the ID from the Slack tool description |
| `journal_lookback` | `7` | how many recent journal entries to read |
| `sources` | all three | which agent session stores to scan |

Precedence is flag, then file, then built-in default. The file is read at run
time, so edits apply on the next run — **no Claude Code restart**, because
nothing here is a hook. Unknown keys are ignored rather than treated as errors.

Lookup order for the file: `--config PATH`, then `$DAILY_REVIEW_CONFIG`, then
`~/.claude/daily-review.local.md`.

The parser handles a deliberately small YAML subset — scalars, inline lists
(`[a, b]`) and block lists (`  - a`). It is not a general YAML parser; nested
maps and multi-line strings are not supported.

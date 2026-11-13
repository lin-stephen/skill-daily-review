---
name: daily-review
description: Run the user's end-of-day reflection. Scans local AI agent session history (Claude Code, Codex CLI, Antigravity), git commits, and Slack activity for a time range (default yesterday), ranks the 7 most significant topics, interviews the user in 繁體中文 to let them recall and reflect, then appends a 做得好的 / 可以更好的 / 有感 / 感謝 entry to their Heptabase journal. Use when the user runs /daily-review, or asks for a daily review, 每日回顧, 日誌, or end-of-day reflection.
---

# Daily Review

A reflection ritual, not a status report. The scan exists to jog the user's
memory; **the user's own recollection is the primary source** and the journal is
written in their voice, not yours.

Run the steps in order. Do not skip the interview and do not write without
approval.

---

## Settings

User settings live in `~/.claude/daily-review.local.md` — repo roots, the work
day boundary, git authors, how many journals to read back. The scripts read it
themselves; you do not need to parse it.

On the **first run only**, check whether it exists:

```bash
python3 ~/.claude/skills/daily-review/scripts/config.py --show
```

If it reports `(not present)`, say so in one line, offer to create it with
`config.py --init`, and carry on with defaults either way — never block the
review on configuration. If it does exist, say nothing; it is working.

The shipped default `repo_roots` are generic (`~/projects`, `~/src`, `~/code`,
`~/work`). A user whose code lives elsewhere will see an empty git section
until the file is created, so mention that if the git scan comes back empty
while the agent sessions clearly show work in repos.

Edits to the file apply on the next run. No restart — nothing here is a hook.

---

## Step 0 — Resolve the time range

A **work day runs 05:00 to 05:00**, not midnight to midnight. Work done after
midnight belongs to the day it started on: a session at 01:30 on Nov 13 is part
of the Nov 12 work day and goes in the Nov 12 journal.

This also shifts what "today" means. Before 05:00, the current work day is still
yesterday's date — so at 02:00 on Nov 13, `/daily-review` (yesterday) means
**Nov 11**, not Nov 12. The scripts compute this; do not reimplement it.

Default: **yesterday's work day**, in local time (Asia/Taipei).

Parse an override from the invocation; accept natural forms:

| Invocation | Range |
|---|---|
| `/daily-review` | yesterday |
| `/daily-review today` | today |
| `/daily-review 2026-11-10` | that single day |
| `/daily-review 11/10` | that day, current year |
| `/daily-review last week` | the previous Mon–Sun |
| `/daily-review 2026-11-10..2026-11-12` | that span |
| `/daily-review 過去三天` | the last 3 full days |

**Multi-day ranges are reviewed as one combined entry**, not day by day. The
journal entry lands in the **last day of the range**.

If the user wants a plain calendar day instead, pass `--day-start 00:00` to both
scripts and use midnight bounds for Slack — or set `day_start: "00:00"` in the
settings file to make it permanent. The boundary may already differ from 05:00
if they configured one, so take the window from the scripts' `Window:` line
rather than assuming.

State the resolved range in one line — give the real bounds, e.g.
「Nov 12 工作日（11/12 05:00 – 11/13 05:00）」 — then start scanning. Do not ask the
user to confirm the range unless the override was genuinely ambiguous.

---

## Step 1 — Scan local AI agent sessions and git

Use the session scratchpad directory for all intermediate files.

```bash
# 1a. agent sessions (Claude Code + Codex CLI + Antigravity)
python3 ~/.claude/skills/daily-review/scripts/scan_agents.py \
  --from YYYY-MM-DD --to YYYY-MM-DD --out <scratch>/agents.md
```

Both scripts default to a 05:00 work-day boundary and print the exact window
they used in their header. Read that line and keep it — Step 2 needs the same
bounds for Slack, and Step 7 states the range back to the user.

Read `<scratch>/agents.md`. Collect the distinct `cwd` paths from its session
headers — those are the repos the user actually worked in.

```bash
# 1b. git commits: session repos first, then a sweep of everything else
python3 ~/.claude/skills/daily-review/scripts/scan_git.py \
  --from YYYY-MM-DD --to YYYY-MM-DD \
  --priority <comma-separated cwds from 1a> \
  --out <scratch>/git.md
```

Both scripts always exit 0. **Always read their `## NOTES / gaps` section** and
carry any gap into Step 7's honesty rule — a source that returned nothing is a
blind spot to disclose, never silently treated as "nothing happened".

`references/data-sources.md` documents the storage formats, the caps the scripts
apply, and what to do when a source misbehaves.

---

## Step 2 — Scan Slack

Follow `references/slack-scan.md` for the exact query recipe. In summary, all
four of these are in scope:

1. Messages the user sent, in channels **and** DMs and group DMs.
2. The threads those messages belong to (replies by others).
3. **In-channel follow-ups** — messages others posted in the same channel after
   the user's, since people here often reply in channel rather than in thread.
4. Messages that `@`-mention the user, even in channels they did not post in.

Get the user's Slack ID from the Slack tool's own description, which states
`Current logged in user's user_id is U…` — do not ask them, and do not assume
an ID from an earlier session. A non-empty `slack_user_id` in the settings file
overrides it.

Also collect **emoji reactions on the user's messages** — who reacted and with
what. The journal quotes these by name (`:ok\_hand:`), so record them exactly.

DM content carries no special restriction: it may inform the analysis and may
appear in the journal the same as channel content.

Use the **same 05:00–05:00 bounds** as Step 1. Slack's `on:` / `after:` /
`before:` query modifiers are whole-calendar-day only and cannot express this,
so pass Unix timestamps to the `after` and `before` **parameters** instead.
`references/slack-scan.md` has the exact calls.

Keep the **channel name and local time** of everything that survives. Step 6
needs them for provenance tags. For a message after midnight, write the time as
`(#channel 01:30)` — the reader knows the work day from the journal date, and
the user's own journals already do this.

---

## Step 3 — Read the previous journals

`references/heptabase-journal.md` has the full command set and the house
format. Minimum:

```bash
heptabase space-instructions read      # once per session, before other commands

heptabase card list --card-types journal --sort createdTime \
  --direction descending --limit 10   # or journal_lookback from settings

heptabase object read journal 2026-11-06 \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['content'])"
```

Read the **most recent `journal_lookback` entries** (default 7) — the user does
not journal every day, so take the N most recent entries that exist rather than
the last N dates. Extract:

- **Voice and shape.** The reference file records the house format, but read
  the real entries anyway: bullet length, how much verbatim quoting, how blunt
  有感 is that week. The new entry must be indistinguishable in style.
- **Open 可以更好的 items.** List what the user said they would do better. In
  Step 5 you will ask whether those actually got addressed. This cross-day
  follow-up is a required part of the interview, not a bonus.

If the `heptabase` CLI is missing or cannot connect, see Failure modes.

---

## Step 4 — Rank the 7 most significant topics

Work out the 7 internally. **Do not show them yet.**

Rank by these three signals:

- **Struggle and friction** — repeated failures, long debugging loops, work
  redone, errors in the `Errors / failures` blocks, `repeated commands`
  markers, commits that revert or fix earlier ones, visible frustration in the
  user's own prompts.
- **Decisions and turning points** — choices of approach or architecture,
  changes of direction mid-session, things explicitly rejected and why.
- **Collaboration signal** — Slack threads that drew replies or reactions,
  where the user unblocked someone, got pushback, or changed someone's mind.
  Also watch for who helped **them**: that is 感謝 material.

**Time and volume alone do not make a topic significant.** A three-hour session
that went smoothly ranks below a twenty-minute exchange where the user was
wrong about something. Say so to yourself when ranking.

For each topic record: a one-line handle, the evidence behind it, and its
**provenance** — session, repo, commit hash, channel, local time. Step 6 needs
that evidence verbatim, so keep it now rather than reconstructing it later.

---

## Step 5 — Interview the user, in 繁體中文

The whole interview runs in Traditional Chinese.

### 5a. Recall first — this is a hard rule

**Ask the user what they remember before revealing anything you found.** Your
7 topics anchor their memory and the entry becomes your summary instead of
their reflection. Say that the scan is done and ask something like:

> 掃完了，先不看我整理的東西 —— 昨天有什麼印象深刻的？順手的、卡住的、或是跟人互動讓你有感覺的都算。

Then wait. Let them answer in full. Do not list, hint at, or number your
findings in this message.

### 5b. Then show your 7, and reconcile

Present the 7 topics compactly, marked against what they just said:

- topics they raised themselves → these matter most, keep them at the top
- topics you found that they did not mention → ask whether it was genuinely
  minor or just slipped their mind
- things they raised that your scan missed → ask where it happened, and say
  plainly that the scan missed it

### 5c. Follow up on the previous 可以更好的

Go through the open items from Step 3: 上次說要…，昨天有做到嗎？ Treat an honest
「沒有」 as useful material, not a failure to smooth over.

### 5d. Fill the gaps

Ask only about what the data genuinely cannot settle, a few questions at most,
and prefer one at a time over a wall of them. Good targets:

- offline events the scan cannot see — meetings, hallway conversations, reading
- why a decision was made, when the log only shows what was done
- how something felt, when the log only shows that it worked
- **who helped them**, for 感謝 — this rarely shows up in a log

Capture the user's own wording. The journal quotes them verbatim, so the exact
phrase they use is the material, not a paraphrase of it.

Stop when the sections have enough to stand on. Do not keep interviewing for
completeness.

---

## Step 6 — Draft the journal entry

**Read `references/heptabase-journal.md` before drafting.** It carries the
house format, the provenance-tag vocabulary and a calibration example. The
summary below is not a substitute for it.

### Shape

Sections in this order, **omitting any that have nothing**:

```
做得好的

- 

可以更好的

- 


有感

- 

感謝

- 
```

Note the spelling 做得好的 (得, not 的). Blank line between every bullet, two
blank lines before each new heading. Typical volume: 做得好的 2–5 bullets,
可以更好的 1–2, 有感 1–3, 感謝 1.

### Provenance tags

End each bullet with where it came from — `(interview)`, `(codex)`,
`(#channel-name 12:52)`, `(9d89a34)`, `(ABC-2705)`, or a combination like
`(#channel-name 22:34, interview)`. `(interview)` specifically means the user
said it and no scan found it. This is the convention that makes the journal
auditable later; reproduce it exactly.

### Rules

- **Only what survived the interview.** The 7 topics were an agenda, not a
  table of contents — a topic the user shrugged at does not belong.
- **Concrete over abstract.** Name the person, the channel, the repo, the
  commit, the ticket. Quote what was actually said, in 「」 for Chinese and ""
  for English — including the user quoting themselves.
- **Do not soften 有感.** The user writes 「好累」 and 壓得我喘不過氣來了 and leaves
  them unresolved. A bullet that ends in an unresolved feeling is correct.
- **可以更好的** names what went wrong, including the user's own mistakes,
  without appending a lesson.
- Write nothing the user did not say or confirm. **No invented feelings.**
  If a section is thin because the day was thin, let it be thin — or drop it.

---

## Step 7 — Approve, then write

Show the full draft and ask for approval. Alongside it, state in one line any
gap from Step 1/2 — e.g. 「Antigravity 沒有可讀的紀錄」 — so the user knows what the
entry is not based on.

Revise on request. **Only after an explicit OK**, append it. Build the JSON
with a script, never by hand — the content is full of quotes and newlines:

```bash
python3 - <<'EOF' | heptabase journal edit --input -
import json
content = open("<scratch>/entry.md", encoding="utf-8").read()
print(json.dumps({"date": "2026-11-12",
                  "edits": [{"type": "append", "content": content}]}))
EOF
```

- target: the journal for the **last day of the reviewed range**
- `append` only — never `overwrite`; the user may have written there already
- the call returns excerpts of the saved content. **Verify from those**, do not
  re-read the journal
- confirm with the journal date and nothing else

---

## Failure modes

| Problem | Do this |
|---|---|
| `heptabase: command not found` | Stop before writing. Ask the user to enable the CLI in the Heptabase desktop app (Settings > AI Features), then give them the finished draft as text so the reflection is not lost. |
| CLI cannot connect | Run `heptabase start` and retry once. If the app is open but still not ready, only the user can turn the CLI on — ask, then retry. |
| `journal edit` fails | Nothing was written, including no new journal. The error names each failed edit and why. Fix and retry once; do not fall back to `overwrite`. |
| A scan source returns nothing | Report it as a gap in Step 7. Never present an empty source as a quiet day. |
| Antigravity has no readable transcripts | Expected; its store is often empty or binary. Mention it once, do not debug it mid-review. |
| Slack returns nothing | Check the date filters use the right timezone before concluding the user was quiet. Slack's `after:`/`before:` modifiers exclude the named day; the `after`/`before` parameters take Unix timestamps and are inclusive. |
| Git section empty but sessions show repo work | The configured `repo_roots` probably miss where their code lives. Run `config.py --show`, and offer `config.py --init` or a direct edit. |
| Work after midnight seems missing | Confirm the scripts ran with the default `--day-start 05:00` and check the `Window:` line in their output. A midnight boundary silently drops late-night work from the day it belongs to. |
| The day really was unremarkable | Say so and write a short honest entry. Do not pad to reach a bullet count. |
| User asks to skip the interview | Allowed, but say the entry will be your summary rather than their reflection, every bullet will be scan-sourced, and 感謝 and most of 有感 will be empty. |

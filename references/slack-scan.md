# Slack scan recipe

All four scopes below are in scope for this user: channels they posted in,
in-channel follow-ups, DMs and group DMs, and mentions of them.

## Identity

Do not ask the user who they are on Slack, and do not hardcode an ID here.
The descriptions of `mcp__plugin_slack_slack__slack_search_public_and_private`
and `slack_search_users` both state it outright:

> Current logged in user's user_id is `U…`.

Read that line from the tool description at run time. It stays correct if the
account changes, and it keeps the ID out of this repo. Below, `<@UID>` stands
for the value you read; Slack search wants the literal angle-bracket form, so
a query reads `from:<@U01ABCDEFGH>` once substituted.

If the user set `slack_user_id` in `~/.claude/daily-review.local.md`, that
overrides what the tool description says. It is empty by default.

## Date filters — the work day is 05:00 to 05:00

A work day runs from 05:00 to 05:00 the next day, so Nov 12 means
**2026-11-12 05:00 → 2026-11-13 05:00** local. Compute both bounds once:

```bash
date -j -f "%Y-%m-%d %H:%M:%S" "2026-11-12 05:00:00" +%s   # after  / oldest
date -j -f "%Y-%m-%d %H:%M:%S" "2026-11-13 05:00:00" +%s   # before / latest
```

**Use the `after` and `before` parameters, not the query modifiers.** The
`on:` / `after:` / `before:` modifiers inside `query` work on whole calendar
days and cannot express an 05:00 boundary — and the modifiers are *exclusive*
of the named day, while the parameters take Unix timestamps and are
*inclusive*. `slack_read_channel` and `slack_read_thread` take the same bounds
as `oldest` / `latest`.

Getting this wrong loses the most interesting part of the day: late-night
incident work, which lands after midnight and is exactly what the journal's
有感 section tends to be about.

## The passes

Semantic search is **not** available on this account — keyword queries only,
space-separated terms are ANDed, and there are no boolean operators.
`limit` maxes out at 20 per call; paginate with the returned `cursor` until the
window is covered.

### Pass 1 — everything the user sent

```
slack_search_public_and_private(
  query = "from:<@UID>",
  after = "<unix ts for 2026-11-12 05:00>",
  before = "<unix ts for 2026-11-13 05:00>",
  sort = "timestamp", sort_dir = "asc", limit = 20,
  channel_types = "public_channel,private_channel,mpim,im",
  include_context = true)
```

This single call covers channels, DMs and group DMs. Page through all results.
From them, build the set of **(channel_id, message_ts)** pairs and the set of
distinct **channel_ids** — both drive the later passes.

### Pass 2 — threads

For every result that is a thread parent or a thread reply:

```
slack_read_thread(channel_id = ..., message_ts = <parent ts>,
                  response_format = "detailed")
```

`detailed` includes reactions and thread metadata, which is what Pass 4 needs.
Use the **parent** ts, not the user's reply ts.

### Pass 3 — in-channel follow-ups

People here often reply in channel rather than in thread, so a reply to the
user may not be linked to their message at all. For each distinct channel from
Pass 1:

```
slack_read_channel(channel_id = ..., oldest = <work-day start ts>,
                   latest = <work-day end ts>, limit = 100,
                   response_format = "detailed")
```

Then look for messages by **other people** that land shortly after one of the
user's messages, or that quote, name or answer it. Judge by content, not just
adjacency — a busy channel will put unrelated messages next to theirs.

### Pass 4 — reactions on the user's messages

`detailed` responses from Passes 2–3 already carry reactions. Where a message
from Pass 1 was not covered by either, fetch them directly:

```
slack_get_reactions(channel_id = ..., message_ts = ...)
```

Record **who** reacted and **with what**. Treat a reaction as collaboration
signal: who is paying attention to the user's work, and what landed well.
A 👀 is not a 🎉.

### Pass 5 — mentions

```
slack_search_public_and_private(
  query = "<@UID>",
  after = "<unix ts for 2026-11-12 05:00>",
  before = "<unix ts for 2026-11-13 05:00>",
  sort = "timestamp", sort_dir = "asc", limit = 20)
```

Drop hits the user wrote themselves — those are already in Pass 1. What is left
is people pulling them in, including in channels they never posted in. An
unanswered mention is worth raising in the interview.

## What to carry into the ranking

For each Slack item that survives, keep: channel name, local time, what the
user said, who responded, how they responded (reply, reaction, silence), and
whether anything was left unresolved.

Silence is a signal too. A proposal that drew no reply is 可以更好的 material
as readily as an argument that went badly.

## Cost control

Pass 1 and Pass 5 are cheap. Passes 2–3 scale with how many channels the user
touched. If Pass 1 returns more than ~10 distinct channels, run Pass 3 only on
channels where the user posted something substantive — a lone emoji reply or a
one-word 收到 does not need its surrounding channel read.

# Heptabase journal: commands and house format

Derived from the user's own journals (8 entries, 2026-10-28 .. 2026-11-06) and
the CLI System Manual, verified 2026-11-13 against CLI 0.11.0.

The format rules below are measured from those entries. **The example is
synthetic** — it mirrors their structure, density and tone without reproducing
their content, so this file stays safe to share. Read the user's actual recent
entries in Step 3 for the real voice.

## Preconditions

The CLI talks to a local server inside the desktop app.

```bash
heptabase --version          # needs >= 0.10.0
heptabase start              # opens the app and waits; prints {"status":"ready"}
```

If a command cannot connect, run `heptabase start` and retry. If the app is
open but the CLI is still not ready, only the user can fix it: ask them to turn
on the CLI in **Heptabase Settings > AI Features**.

Run `heptabase space-instructions read` once per session before other commands.
(Checked 2026-11-13: this space has none. If that changes, Space Instructions
outrank this file.)

## Reading

```bash
# which journals exist, newest first
heptabase card list --card-types journal --sort createdTime \
  --direction descending --limit 10

# one journal, as line-numbered Hepta Markdown inside a JSON envelope
heptabase object read journal 2026-11-06
```

`object read` returns `{"content": "...", "totalLines": N}`. Strip the JSON and
the leading `<lineNumber><tab>` prefixes before reusing any of it as text:

```bash
heptabase object read journal 2026-11-06 \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['content'])"
```

A journal is created on the day **after** the day it covers — the 2026-11-06
journal was created 2026-11-07. Writing into the reviewed day's journal is
therefore the established habit, not a new convention.

## Writing

`journal edit` takes JSON on stdin and creates the journal if that date has
none. **Append — never overwrite.**

```bash
printf '%s' "$JSON" | heptabase journal edit --input -
```

where `$JSON` is:

```json
{
  "date": "2026-11-12",
  "edits": [{ "type": "append", "content": "做得好的\n\n- ...\n" }]
}
```

Build the JSON with `jq` or a temp file written by Python — never hand-written
shell interpolation, since the content contains quotes, brackets and newlines.

Rules that matter here:

- All edits to one journal go in **one** call; it is written whole or not at
  all, and a failure writes nothing.
- `append` adds after the last block that has text.
- A successful call returns excerpts of the saved content with line numbers.
  **Use those to verify — do not re-read the journal.**
- `journal edit` marks a journal as *Created by AI* **only when it creates
  one**. Appending to an existing journal never adds the mark. So a day the
  user already started stays unmarked; a day they never opened gets marked.
  Mention this if they care about the Created-by-AI filter.
- No source citations. The manual asks for them when content comes from
  Heptabase objects; here it comes from the user's own day and interview, so
  citations do not apply. The provenance tags below do that job instead.

## House format

### Sections

Only sections with content are written. Observed across 8 consecutive journals:

| Section | Frequency | Typical bullets |
|---|---|---|
| `做得好的` | 8/8 — always | 2–5 |
| `可以更好的` | 3/8 — only when there is something | 1–2 |
| `有感` | 8/8 — always | 1–3 |
| `感謝` | 6/8 — a regular section, not an extra | 1 |

Canonical order: **做得好的 / 可以更好的 / 有感 / 感謝**. The user has shuffled
有感 and 感謝 occasionally; keep the canonical order unless they ask otherwise.

Note the spelling: **做得好的**, with 得 — not 做的好的.

### Layout

A blank line between the heading and the first bullet, a blank line between
every bullet, and **two** blank lines before the next heading:

```
做得好的

- 第一條

- 第二條


可以更好的

- 第一條


有感

- 第一條
```

### Provenance tags

Almost every bullet ends with its source in parentheses. This is the
convention that makes the journal auditable later, and it is the single most
important thing to reproduce.

| Tag | Means |
|---|---|
| `(interview)` | the user said it during the interview; not in any scanned source |
| `(codex)` | a Codex CLI session |
| `(#channel-name 09:19)` | a Slack channel and local time |
| `(#channel-name 16:55–17:01)` | a span, for a conversation |
| `(9d89a34, 59c755b)` | git short hashes |
| `(api-gateway 3f21ab0 #4471)` | repo, hash, and MR number |
| `(ABC-2705)` / `(SD-10042)` | Jira tickets |
| `(#channel-name 22:34, interview)` | combined — both sources |

Separators inside a tag are `,` or `、`, both in use. A bullet whose source is
obvious from its own text may carry no tag at all. Add `(claude-code)` for a
Claude Code session by analogy with `(codex)` — the user has not needed it yet,
so say once that it is a new tag.

### Voice

From the user's own entries:

- First person, past tense, concrete. Real names, real channels, real ticket
  and commit IDs — never 「同事」 where a name belongs.
- Direct quotes of what was actually said, in 「」 for Chinese and "" for
  English, including the user quoting themselves: 我回「…」.
- 「——」 attributes a quote to its speaker.
- Slack emoji by name, with underscores escaped: `:ok\_hand:`,
  `:raised\_hands::skin-tone-5:`, `:smiling\_face\_with\_tear:`. Escape
  underscores in channel names too: `#some\_channel\_name`.
- Inline Markdown links where a URL is the point: `[短標題](https://...)`.
- 有感 is blunt and often unflattering, including about colleagues and about
  the user themselves. **Do not soften it.** Bullets that end in an unresolved
  feeling are correct, not incomplete, and a tired one-word quote like 「好累」
  carries more than a tidy paragraph would.
- 可以更好的 names what actually went wrong, including the user's own mistakes,
  without a lesson attached.
- 感謝 is one line naming a person and what they did.

### A calibration example (synthetic)

Invented people, channels and repos. The point is the **density**: who asked,
what the user said verbatim, what turned out to be true, who corrected it, and
where it happened.

```
做得好的

- #ch-support 的 checkout latency alert，Amy 叫我看，我對照 Ben 貼的 IP 回「these IP are from the partner VPN, not real user traffic」和「no need to tune the alert on our side」，Ben 回 :ok\_hand:

- 晚上把 api-gateway 的 rate limiter 換成 sliding window，壓測跑完才 commit (3f21ab0, codex)


可以更好的

- Carol 問能不能在 staging 開 real account 給 demo 用，我回「I'm pretty sure we block that」。其實我只是依稀記得有條 test- 前綴的規則，去翻 auth-service 的 code 才發現那條規則只擋 login name，沒擋 real account，Dan 後來在 staging 試成功 (#dept-eng 12:52, interview)


有感

- 整天在 war room 等別人的結論，自己沒有產出，「這種等待比加班還累」 (interview)

感謝

- Ben 幫我 review 了三個 repo 的 release (#ch-release)
```

Measure a draft against this: 「溝通可以更清楚」 or 「今天學到很多」 would be a
failure by this standard, because neither survives being read back in a month.

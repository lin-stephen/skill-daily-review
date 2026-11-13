#!/usr/bin/env python3
"""Condense local AI agent session history into a reviewable digest.

Sources
  claude-code   ~/.claude/projects/<slug>/<session-uuid>.jsonl
  codex         ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl
  antigravity   ~/.gemini/antigravity-cli/conversation_summaries.db

Usage
  scan_agents.py --from 2026-11-12 --to 2026-11-12
  scan_agents.py --from 2026-11-10 --to 2026-11-12 --json
  scan_agents.py --from 2026-11-12 --to 2026-11-12 --source codex
  scan_agents.py --from 2026-11-12 --day-start 00:00   # calendar day instead

Defaults come from ~/.claude/daily-review.local.md when it exists; a flag
passed here overrides it. See config.py.

Dates name LOCAL *work days*, inclusive on both ends. A work day runs from
--day-start (default 05:00) to the same time the next day, so a session at
01:30 on Nov 13 belongs to the Nov 12 work day.
Exit code is 0 even when a source yields nothing; check the NOTES section.
"""

import argparse
import json
import re
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, time as dtime, timedelta
from pathlib import Path

HOME = Path.home()
CLAUDE_DIR = HOME / ".claude" / "projects"
CODEX_DIR = HOME / ".codex" / "sessions"
ANTIGRAVITY_DB = HOME / ".gemini" / "antigravity-cli" / "conversation_summaries.db"

# Truncation caps keep the digest readable without losing the signal that matters.
MAX_USER_CHARS = 1500
MAX_ASSIST_CHARS = 350
MAX_ASSIST_MSGS = 25
MAX_ERRORS = 20
MAX_ERR_CHARS = 220
MAX_FILES = 40
MAX_CMDS = 25
MAX_CMD_CHARS = 140
MAX_SESSIONS = 60

# A "work day" runs from this local time to the same time the next day, so work
# done after midnight still belongs to the day it started on.
DAY_START_DEFAULT = "05:00"
DEFAULTS_FALLBACK = {"day_start": DAY_START_DEFAULT,
                     "sources": ["claude", "codex", "antigravity"]}

# config.py sits next to this script; tolerate its absence so the scanner still
# runs standalone.
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import config as drconfig
except ImportError:                                    # pragma: no cover
    drconfig = None


def load_settings(explicit):
    if drconfig is None:
        return dict(DEFAULTS_FALLBACK), {}
    return drconfig.load(explicit)

LOCAL_TZ = datetime.now().astimezone().tzinfo


def parse_day_start(value):
    """'5', '05', '5:30' or '05:00' -> (hour, minute)."""
    raw = str(value).strip()
    hh, _, mm = raw.partition(":")
    try:
        hour, minute = int(hh), int(mm or 0)
    except ValueError:
        raise ValueError("--day-start must look like 5 or 05:00, got %r" % value)
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise ValueError("--day-start out of range: %r" % value)
    return hour, minute


def work_day_window(d_from, d_to, hour, minute):
    """Inclusive work-day range -> [start, end) wall-clock bounds."""
    start = datetime.combine(d_from, dtime(hour, minute)).replace(tzinfo=LOCAL_TZ)
    end = datetime.combine(d_to, dtime(hour, minute)).replace(tzinfo=LOCAL_TZ) \
        + timedelta(days=1)
    return start, end


def current_work_day(hour, minute, now=None):
    """The work day 'now' belongs to: before the cutoff, that is yesterday."""
    now = now or datetime.now(LOCAL_TZ)
    return (now - timedelta(hours=hour, minutes=minute)).date()

_WS = re.compile(r"\s+")
_FRAC = re.compile(r"(\.\d{6})\d+")
_NOISE_PREFIXES = (
    "<local-command-stdout>",
    "<command-message>",
    "<environment_context>",
    "<user_instructions>",
    "Caveat: The messages below",
    "[Request interrupted",
    "<task-notification>",
    "<local-command-caveat>",
    "<system-reminder>",
    "Base directory for this skill:",
)


def norm(text, limit):
    s = _WS.sub(" ", str(text or "")).strip()
    if len(s) > limit:
        s = s[:limit].rstrip() + " ...[truncated]"
    return s


def parse_ts(value):
    """Parse an ISO-8601 string or epoch number into a local-tz datetime."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        if v > 1e11:
            v /= 1000.0
        try:
            return datetime.fromtimestamp(v, tz=LOCAL_TZ)
        except (OverflowError, OSError, ValueError):
            return None
    s = str(value).strip()
    if not s:
        return None
    s = s.replace(" UTC", "").replace(" +0000", "+00:00")
    s = re.sub(r"\s+m=[-+][\d.]+$", "", s)
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    s = _FRAC.sub(r"\1", s)
    s = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", s)
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=LOCAL_TZ)
    return dt.astimezone(LOCAL_TZ)


def is_noise(text):
    t = (text or "").lstrip()
    return any(t.startswith(p) for p in _NOISE_PREFIXES)


def stringify(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict):
                parts.append(b.get("text") or b.get("content") or "")
            else:
                parts.append(str(b))
        return " ".join(str(p) for p in parts)
    return str(content or "")


def new_session(source, sid, cwd, origin):
    return {
        "source": source,
        "id": str(sid or "")[:8],
        "cwd": cwd or "",
        "branch": "",
        "model": "",
        "origin": str(origin),
        "first_ts": None,
        "last_ts": None,
        "user_turns": [],
        "assistant": [],
        "tools": {},
        "errors": [],
        "files": set(),
        "commands": [],
        "sidechain_turns": 0,
    }


def touch(sess, ts):
    if ts is None:
        return
    if sess["first_ts"] is None or ts < sess["first_ts"]:
        sess["first_ts"] = ts
    if sess["last_ts"] is None or ts > sess["last_ts"]:
        sess["last_ts"] = ts


# --------------------------------------------------------------------------
# Claude Code
# --------------------------------------------------------------------------
def scan_claude(start, end):
    notes = []
    sessions = []
    if not CLAUDE_DIR.is_dir():
        return sessions, ["claude-code: %s does not exist" % CLAUDE_DIR]

    files = sorted(CLAUDE_DIR.glob("*/*.jsonl"))
    if not files:
        return sessions, ["claude-code: no session files under %s" % CLAUDE_DIR]

    scanned = 0
    for f in files:
        try:
            if datetime.fromtimestamp(f.stat().st_mtime, tz=LOCAL_TZ) < start:
                continue
        except OSError:
            continue
        scanned += 1
        sess = None
        try:
            fh = f.open("r", encoding="utf-8", errors="replace")
        except OSError:
            continue
        with fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(e, dict):
                    continue
                etype = e.get("type")
                if etype not in ("user", "assistant"):
                    continue
                ts = parse_ts(e.get("timestamp"))
                if ts is None or not (start <= ts < end):
                    continue
                if sess is None:
                    sess = new_session("claude-code", e.get("sessionId") or f.stem,
                                       e.get("cwd"), f)
                if not sess["cwd"] and e.get("cwd"):
                    sess["cwd"] = e["cwd"]
                if not sess["branch"] and e.get("gitBranch"):
                    sess["branch"] = e["gitBranch"]
                touch(sess, ts)

                side = bool(e.get("isSidechain"))
                msg = e.get("message") or {}
                content = msg.get("content")
                if not sess["model"] and msg.get("model"):
                    sess["model"] = msg["model"]

                if etype == "user":
                    is_result = e.get("toolUseResult") is not None or (
                        isinstance(content, list)
                        and any(isinstance(b, dict) and b.get("type") == "tool_result"
                                for b in content)
                    )
                    if is_result:
                        if isinstance(content, list):
                            for b in content:
                                if (isinstance(b, dict) and b.get("type") == "tool_result"
                                        and b.get("is_error")):
                                    sess["errors"].append(
                                        (ts, "tool_result",
                                         norm(stringify(b.get("content")), MAX_ERR_CHARS)))
                        continue
                    if side:
                        sess["sidechain_turns"] += 1
                        continue
                    txt = content if isinstance(content, str) else " ".join(
                        b.get("text", "") for b in (content or [])
                        if isinstance(b, dict) and b.get("type") == "text")
                    if txt.strip() and not is_noise(txt):
                        sess["user_turns"].append((ts, norm(txt, MAX_USER_CHARS)))
                    continue

                # assistant
                if not isinstance(content, list):
                    continue
                for b in content:
                    if not isinstance(b, dict):
                        continue
                    bt = b.get("type")
                    if bt == "text" and not side:
                        t = b.get("text", "")
                        if t.strip():
                            sess["assistant"].append((ts, norm(t, MAX_ASSIST_CHARS)))
                    elif bt == "tool_use":
                        name = b.get("name") or "?"
                        sess["tools"][name] = sess["tools"].get(name, 0) + 1
                        inp = b.get("input") or {}
                        if isinstance(inp, dict):
                            fp = (inp.get("file_path") or inp.get("path")
                                  or inp.get("notebook_path"))
                            if fp:
                                sess["files"].add(str(fp))
                            if name == "Bash" and inp.get("command"):
                                sess["commands"].append(norm(inp["command"], MAX_CMD_CHARS))
        if sess and (sess["user_turns"] or sess["tools"] or sess["assistant"]):
            sessions.append(sess)

    if scanned == 0:
        notes.append("claude-code: no session file was modified on/after %s"
                     % start.date())
    elif not sessions:
        notes.append("claude-code: %d file(s) checked, none had entries inside the window"
                     % scanned)
    return sessions, notes


# --------------------------------------------------------------------------
# Codex CLI
# --------------------------------------------------------------------------
def scan_codex(start, end):
    notes = []
    sessions = []
    if not CODEX_DIR.is_dir():
        return sessions, ["codex: %s does not exist" % CODEX_DIR]

    files = [p for p in CODEX_DIR.rglob("*.jsonl") if p.is_file()]
    if not files:
        return sessions, ["codex: no rollout files under %s" % CODEX_DIR]

    scanned = 0
    for f in sorted(files):
        try:
            if datetime.fromtimestamp(f.stat().st_mtime, tz=LOCAL_TZ) < start:
                continue
        except OSError:
            continue
        scanned += 1
        sess = None
        fallback_user = []
        fallback_assist = []
        try:
            fh = f.open("r", encoding="utf-8", errors="replace")
        except OSError:
            continue
        with fh:
            meta_cwd = ""
            meta_id = ""
            meta_model = ""
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(e, dict):
                    continue
                payload = e.get("payload") or {}
                etype = e.get("type")

                if etype == "session_meta":
                    meta_cwd = payload.get("cwd") or meta_cwd
                    meta_id = payload.get("id") or payload.get("session_id") or meta_id
                    continue
                if etype == "turn_context":
                    meta_cwd = payload.get("cwd") or meta_cwd
                    meta_model = payload.get("model") or meta_model
                    continue

                ts = parse_ts(e.get("timestamp"))
                if ts is None or not (start <= ts < end):
                    continue

                ptype = payload.get("type")
                if ptype == "item_completed":
                    item = payload.get("item") or {}
                    itype = item.get("type") or item.get("item_type")
                    if sess is None:
                        sess = new_session("codex", meta_id or f.stem, meta_cwd, f)
                        sess["model"] = meta_model
                    touch(sess, ts)
                    if itype == "UserMessage":
                        txt = stringify(item.get("content"))
                        if txt.strip() and not is_noise(txt):
                            sess["user_turns"].append((ts, norm(txt, MAX_USER_CHARS)))
                    elif itype == "AgentMessage":
                        txt = stringify(item.get("content"))
                        if txt.strip():
                            sess["assistant"].append((ts, norm(txt, MAX_ASSIST_CHARS)))
                    elif itype == "CommandExecution":
                        sess["tools"]["shell"] = sess["tools"].get("shell", 0) + 1
                        cmd = item.get("command")
                        if isinstance(cmd, list):
                            cmd = " ".join(str(c) for c in cmd[2:]) or " ".join(
                                str(c) for c in cmd)
                        cmd = norm(cmd, MAX_CMD_CHARS)
                        if cmd:
                            sess["commands"].append(cmd)
                        try:
                            code = int(item.get("exit_code", 0) or 0)
                        except (TypeError, ValueError):
                            code = 0
                        if code != 0:
                            out = norm(item.get("aggregated_output"), MAX_ERR_CHARS)
                            sess["errors"].append(
                                (ts, "shell exit %d" % code, "%s -- %s" % (cmd, out)))
                    elif itype in ("FileChange", "PatchApply"):
                        for p in (item.get("paths") or []):
                            sess["files"].add(str(p))
                    continue

                if etype == "response_item" and ptype == "message":
                    role = payload.get("role")
                    txt = stringify(payload.get("content"))
                    if not txt.strip() or is_noise(txt):
                        continue
                    if role == "user":
                        fallback_user.append((ts, norm(txt, MAX_USER_CHARS)))
                    elif role == "assistant":
                        fallback_assist.append((ts, norm(txt, MAX_ASSIST_CHARS)))

        if sess is None and (fallback_user or fallback_assist):
            sess = new_session("codex", meta_id or f.stem, meta_cwd, f)
            sess["model"] = meta_model
            for ts, _ in fallback_user + fallback_assist:
                touch(sess, ts)
        if sess is not None:
            if not sess["user_turns"]:
                sess["user_turns"] = fallback_user
            if not sess["assistant"]:
                sess["assistant"] = fallback_assist
            if sess["user_turns"] or sess["tools"] or sess["assistant"]:
                sessions.append(sess)

    if scanned == 0:
        notes.append("codex: no rollout file was modified on/after %s" % start.date())
    elif not sessions:
        notes.append("codex: %d file(s) checked, none had entries inside the window"
                     % scanned)
    return sessions, notes


# --------------------------------------------------------------------------
# Antigravity CLI
# --------------------------------------------------------------------------
def scan_antigravity(start, end):
    notes = []
    sessions = []
    if not ANTIGRAVITY_DB.exists():
        return sessions, ["antigravity: %s not found (CLI not installed?)" % ANTIGRAVITY_DB]

    # The live db is WAL and often held open by the CLI, so mode=ro fails outright.
    # Snapshot it (plus any -wal/-shm sidecars) into a temp dir and read that.
    tmpdir = tempfile.mkdtemp(prefix="daily-review-ag-")
    snap = Path(tmpdir) / ANTIGRAVITY_DB.name
    try:
        shutil.copy2(ANTIGRAVITY_DB, snap)
        for suffix in ("-wal", "-shm"):
            side = Path(str(ANTIGRAVITY_DB) + suffix)
            if side.exists():
                shutil.copy2(side, Path(str(snap) + suffix))
        conn = sqlite3.connect(str(snap))
    except (OSError, sqlite3.Error) as exc:
        shutil.rmtree(tmpdir, ignore_errors=True)
        return sessions, ["antigravity: cannot open db (%s)" % exc]

    conn.row_factory = sqlite3.Row
    try:
        total = conn.execute("select count(*) from conversation_summaries").fetchone()[0]
    except sqlite3.Error as exc:
        conn.close()
        shutil.rmtree(tmpdir, ignore_errors=True)
        return sessions, ["antigravity: unexpected schema (%s)" % exc]

    if total == 0:
        conn.close()
        shutil.rmtree(tmpdir, ignore_errors=True)
        return sessions, [
            "antigravity: conversation_summaries is EMPTY (0 rows). The CLI keeps "
            "transcripts in ~/.gemini/antigravity-cli/implicit/*.pb, which are not "
            "plain text. Nothing is recoverable for this window -- say so rather "
            "than implying Antigravity went unused."]

    try:
        rows = conn.execute(
            "select conversation_id, title, preview, step_count, workspace_uris, "
            "last_user_input_time, last_modified_time from conversation_summaries"
        ).fetchall()
    except sqlite3.Error as exc:
        conn.close()
        shutil.rmtree(tmpdir, ignore_errors=True)
        return sessions, ["antigravity: query failed (%s)" % exc]

    for r in rows:
        ts = parse_ts(r["last_user_input_time"]) or parse_ts(r["last_modified_time"])
        if ts is None or not (start <= ts < end):
            continue
        cwd = ""
        raw_ws = r["workspace_uris"] or ""
        try:
            parsed = json.loads(raw_ws)
            if isinstance(parsed, list) and parsed:
                cwd = str(parsed[0])
        except ValueError:
            cwd = raw_ws
        sess = new_session("antigravity", r["conversation_id"], cwd, ANTIGRAVITY_DB)
        sess["first_ts"] = ts
        sess["last_ts"] = ts
        title = norm(r["title"], 160)
        preview = norm(r["preview"], MAX_USER_CHARS)
        if title:
            sess["user_turns"].append((ts, "[title] %s" % title))
        if preview and preview != title:
            sess["user_turns"].append((ts, preview))
        try:
            frows = conn.execute(
                "select user_body, agent_body from conversations_fts "
                "where conversation_id = ?", (r["conversation_id"],)).fetchall()
            for fr in frows:
                ub = norm(fr["user_body"], MAX_USER_CHARS)
                ab = norm(fr["agent_body"], MAX_ASSIST_CHARS)
                if ub:
                    sess["user_turns"].append((ts, ub))
                if ab:
                    sess["assistant"].append((ts, ab))
        except sqlite3.Error:
            pass
        sessions.append(sess)

    conn.close()
    shutil.rmtree(tmpdir, ignore_errors=True)
    if not sessions:
        notes.append("antigravity: %d conversation(s) stored, none inside the window"
                     % total)
    return sessions, notes


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------
def repeated_commands(commands):
    counts = {}
    for c in commands:
        key = c[:60]
        counts[key] = counts.get(key, 0) + 1
    return sorted(((n, c) for c, n in counts.items() if n >= 3), reverse=True)


def hhmm(dt):
    return dt.strftime("%H:%M") if dt else "??:??"


def render(sessions, notes, start, end):
    out = []
    last_day = (end - timedelta(days=1)).date()
    span = "%s" % start.date()
    if last_day != start.date():
        span = "%s .. %s" % (start.date(), last_day)
    out.append("# AI agent sessions -- work day %s" % span)
    out.append("")
    out.append("Window: %s -> %s (%s)"
               % (start.strftime("%Y-%m-%d %H:%M"), end.strftime("%Y-%m-%d %H:%M"),
                  LOCAL_TZ))
    out.append("")

    by_source = {}
    for s in sessions:
        by_source.setdefault(s["source"], []).append(s)
    summary = ", ".join("%s=%d" % (k, len(v)) for k, v in sorted(by_source.items()))
    out.append("Sessions found: %d (%s)" % (len(sessions), summary or "none"))
    out.append("")

    if notes:
        out.append("## NOTES / gaps")
        for n in notes:
            out.append("- %s" % n)
        out.append("")

    if not sessions:
        out.append("_No agent sessions in this window._")
        return "\n".join(out)

    sessions.sort(key=lambda s: (s["first_ts"] or datetime.max.replace(tzinfo=LOCAL_TZ)))
    if len(sessions) > MAX_SESSIONS:
        out.append("_Capped at %d sessions (of %d); showing the earliest._"
                   % (MAX_SESSIONS, len(sessions)))
        out.append("")
        sessions = sessions[:MAX_SESSIONS]

    for s in sessions:
        dur = ""
        if s["first_ts"] and s["last_ts"]:
            mins = int((s["last_ts"] - s["first_ts"]).total_seconds() // 60)
            dur = " (%dm)" % mins
        label = s["cwd"] or "(unknown cwd)"
        out.append("## [%s] %s" % (s["source"], label))
        meta = ["session %s" % s["id"], "%s-%s%s" % (hhmm(s["first_ts"]),
                                                     hhmm(s["last_ts"]), dur)]
        if s["branch"]:
            meta.append("branch %s" % s["branch"])
        if s["model"]:
            meta.append("model %s" % s["model"])
        out.append("- " + " | ".join(meta))
        tool_total = sum(s["tools"].values())
        counts = ["%d user turn(s)" % len(s["user_turns"]),
                  "%d agent msg(s)" % len(s["assistant"]),
                  "%d tool call(s)" % tool_total,
                  "%d error(s)" % len(s["errors"])]
        if s["sidechain_turns"]:
            counts.append("%d subagent turn(s)" % s["sidechain_turns"])
        out.append("- " + " | ".join(counts))
        if s["tools"]:
            top = sorted(s["tools"].items(), key=lambda kv: -kv[1])[:8]
            out.append("- tools: " + ", ".join("%s x%d" % (k, v) for k, v in top))
        if s["files"]:
            files = sorted(s["files"])
            shown = files[:MAX_FILES]
            extra = "" if len(files) <= MAX_FILES else " (+%d more)" % (len(files) - MAX_FILES)
            out.append("- files touched: " + ", ".join(shown) + extra)
        rep = repeated_commands(s["commands"])
        if rep:
            out.append("- repeated commands (possible friction): "
                       + "; ".join("%dx %s" % (n, c) for n, c in rep[:5]))
        out.append("")

        if s["user_turns"]:
            out.append("**User turns**")
            for ts, txt in s["user_turns"]:
                out.append("- [%s] %s" % (hhmm(ts), txt))
            out.append("")
        if s["errors"]:
            out.append("**Errors / failures**")
            for ts, kind, txt in s["errors"][:MAX_ERRORS]:
                out.append("- [%s] %s: %s" % (hhmm(ts), kind, txt))
            if len(s["errors"]) > MAX_ERRORS:
                out.append("- ...(+%d more)" % (len(s["errors"]) - MAX_ERRORS))
            out.append("")
        if s["assistant"]:
            out.append("**Agent replies (truncated)**")
            for ts, txt in s["assistant"][:MAX_ASSIST_MSGS]:
                out.append("- [%s] %s" % (hhmm(ts), txt))
            if len(s["assistant"]) > MAX_ASSIST_MSGS:
                out.append("- ...(+%d more)" % (len(s["assistant"]) - MAX_ASSIST_MSGS))
            out.append("")
    return "\n".join(out)


def to_json(sessions, notes):
    def ser(s):
        d = dict(s)
        d["files"] = sorted(s["files"])
        d["first_ts"] = s["first_ts"].isoformat() if s["first_ts"] else None
        d["last_ts"] = s["last_ts"].isoformat() if s["last_ts"] else None
        d["user_turns"] = [[t.isoformat(), x] for t, x in s["user_turns"]]
        d["assistant"] = [[t.isoformat(), x] for t, x in s["assistant"]]
        d["errors"] = [[t.isoformat(), k, x] for t, k, x in s["errors"]]
        return d
    return json.dumps({"sessions": [ser(s) for s in sessions], "notes": notes},
                      ensure_ascii=False, indent=2)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="date_from", default=None,
                    help="start work day YYYY-MM-DD (local, inclusive); "
                         "defaults to the work day before the current one")
    ap.add_argument("--day-start", default=None,
                    help="local time a work day begins (default %s); work after "
                         "midnight counts toward the previous day"
                         % DAY_START_DEFAULT)
    ap.add_argument("--config", default=None,
                    help="settings file (default ~/.claude/daily-review.local.md)")
    ap.add_argument("--to", dest="date_to", default=None,
                    help="end date YYYY-MM-DD (local, inclusive); defaults to --from")
    ap.add_argument("--source", action="append", default=None,
                    choices=["claude", "codex", "antigravity"],
                    help="limit to one source (repeatable)")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of markdown")
    ap.add_argument("--out", default=None, help="write to this file instead of stdout")
    args = ap.parse_args()

    settings, _ = load_settings(args.config)
    day_start = args.day_start or settings.get("day_start") or DAY_START_DEFAULT
    try:
        hour, minute = parse_day_start(day_start)
    except ValueError as exc:
        sys.stderr.write("%s\n" % exc)
        return 2

    date_from = args.date_from or str(current_work_day(hour, minute)
                                      - timedelta(days=1))
    try:
        d_from = datetime.strptime(date_from, "%Y-%m-%d").date()
        d_to = datetime.strptime(args.date_to or date_from, "%Y-%m-%d").date()
    except ValueError:
        sys.stderr.write("dates must be YYYY-MM-DD\n")
        return 2
    if d_to < d_from:
        d_from, d_to = d_to, d_from

    start, end = work_day_window(d_from, d_to, hour, minute)

    configured = settings.get("sources") or DEFAULTS_FALLBACK["sources"]
    wanted = set(args.source or configured)
    sessions, notes = [], []
    for name, fn in (("claude", scan_claude), ("codex", scan_codex),
                     ("antigravity", scan_antigravity)):
        if name not in wanted:
            continue
        try:
            s, n = fn(start, end)
        except Exception as exc:  # never let one source kill the run
            s, n = [], ["%s: scan failed (%s: %s)" % (name, type(exc).__name__, exc)]
        sessions.extend(s)
        notes.extend(n)

    text = to_json(sessions, notes) if args.json else render(sessions, notes, start, end)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        sys.stderr.write("wrote %s (%d sessions)\n" % (args.out, len(sessions)))
    else:
        sys.stdout.write(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

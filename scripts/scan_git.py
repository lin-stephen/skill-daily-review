#!/usr/bin/env python3
"""List commits authored by the user across local repos in a date range.

Strategy: repos passed via --priority (taken from the cwd of that day's agent
sessions) are scanned first and always reported, then every other repo found
under --roots is swept so commits made without an agent are not missed.

Usage
  scan_git.py --from 2026-11-12 --to 2026-11-12
  scan_git.py --from 2026-11-12 --to 2026-11-12 \
      --priority /path/repo-a,/path/repo-b --roots ~/projects ~/work
"""

import argparse
import os
import subprocess
import sys
from datetime import datetime, time as dtime, timedelta
from pathlib import Path

DEFAULT_ROOTS = ["~/projects", "~/src", "~/code", "~/work"]
FIND_DEPTH = 6
# Keep in step with scan_agents.py: a work day runs from this local time to the
# same time the next day.
DAY_START_DEFAULT = "05:00"
DEFAULTS_FALLBACK = {"day_start": DAY_START_DEFAULT,
                     "repo_roots": DEFAULT_ROOTS, "git_authors": [],
                     "repo_depth": FIND_DEPTH}

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
GIT_TIMEOUT = 25
SEP = "\x1f"
MARK = "@@C@@"


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


def current_work_day(hour, minute, now=None):
    """The work day 'now' belongs to: before the cutoff, that is yesterday."""
    now = now or datetime.now().astimezone()
    return (now - timedelta(hours=hour, minutes=minute)).date()


def run(args, cwd=None, timeout=GIT_TIMEOUT):
    try:
        p = subprocess.run(args, cwd=cwd, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=timeout)
        return p.returncode, p.stdout.decode("utf-8", "replace"), \
            p.stderr.decode("utf-8", "replace")
    except (subprocess.TimeoutExpired, OSError) as exc:
        return 1, "", str(exc)


def default_authors():
    out = []
    for key in ("user.email", "user.name"):
        rc, so, _ = run(["git", "config", "--global", key])
        if rc == 0 and so.strip():
            out.append(so.strip())
    return out


def find_repos(roots, depth=FIND_DEPTH):
    repos = []
    seen = set()
    for root in roots:
        base = Path(os.path.expanduser(root))
        if not base.is_dir():
            continue
        rc, so, _ = run(["find", str(base), "-maxdepth", str(depth),
                         "-name", ".git"], timeout=90)
        for line in so.splitlines():
            line = line.strip()
            if not line:
                continue
            repo = str(Path(line).parent)
            if repo not in seen:
                seen.add(repo)
                repos.append(repo)
    return repos


def commits_for(repo, authors, since, until):
    fmt = MARK + SEP.join(["%h", "%ad", "%an", "%s", "%d"])
    args = ["git", "-C", repo, "log", "--all", "--no-merges",
            "--since", since, "--until", until,
            "--date=format:%Y-%m-%d %H:%M", "--pretty=format:" + fmt,
            "--shortstat"]
    for a in authors:
        args.insert(5, "--author=" + a)
    rc, so, se = run(args, )
    if rc != 0:
        return [], se.strip().splitlines()[:1]
    commits = []
    current = None
    for line in so.splitlines():
        if line.startswith(MARK):
            if current:
                commits.append(current)
            parts = line[len(MARK):].split(SEP)
            while len(parts) < 5:
                parts.append("")
            current = {"hash": parts[0], "date": parts[1], "author": parts[2],
                       "subject": parts[3], "refs": parts[4].strip(), "stat": ""}
        elif line.strip() and current is not None and "changed" in line:
            current["stat"] = line.strip()
    if current:
        commits.append(current)
    # --all can surface the same commit once; dedupe defensively.
    out, seen = [], set()
    for c in commits:
        if c["hash"] in seen:
            continue
        seen.add(c["hash"])
        out.append(c)
    out.sort(key=lambda c: c["date"])
    return out, []


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="date_from", default=None,
                    help="start work day YYYY-MM-DD; defaults to the work day "
                         "before the current one")
    ap.add_argument("--to", dest="date_to", default=None)
    ap.add_argument("--day-start", default=None,
                    help="local time a work day begins (default %s)"
                         % DAY_START_DEFAULT)
    ap.add_argument("--config", default=None,
                    help="settings file (default ~/.claude/daily-review.local.md)")
    ap.add_argument("--author", action="append", default=None,
                    help="git --author pattern (repeatable); defaults to global "
                         "user.email and user.name")
    ap.add_argument("--priority", default="",
                    help="comma-separated repo paths to scan first "
                         "(usually the cwds seen in that day's agent sessions)")
    ap.add_argument("--roots", nargs="*", default=None)
    ap.add_argument("--depth", type=int, default=None,
                    help="how deep under each root to look for .git "
                         "(default %d)" % FIND_DEPTH)
    ap.add_argument("--no-sweep", action="store_true",
                    help="scan only --priority repos")
    ap.add_argument("--out", default=None)
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

    # git --since/--until are inclusive, so stop one second before the cutoff.
    start = datetime.combine(d_from, dtime(hour, minute))
    end = datetime.combine(d_to, dtime(hour, minute)) + timedelta(days=1) \
        - timedelta(seconds=1)
    since = start.strftime("%Y-%m-%d %H:%M:%S")
    until = end.strftime("%Y-%m-%d %H:%M:%S")

    roots = args.roots if args.roots is not None else (
        settings.get("repo_roots") or DEFAULT_ROOTS)
    try:
        depth = int(args.depth if args.depth is not None
                    else settings.get("repo_depth") or FIND_DEPTH)
    except (TypeError, ValueError):
        depth = FIND_DEPTH
    depth = max(1, min(depth, 10))
    authors = args.author or settings.get("git_authors") or default_authors()
    authors = [str(a) for a in authors if str(a).strip()]
    if not authors:
        sys.stderr.write("no author pattern: set git config --global user.email\n")
        return 2

    priority = []
    for raw in args.priority.split(","):
        raw = raw.strip()
        if not raw:
            continue
        p = Path(os.path.expanduser(raw))
        while p != p.parent and not (p / ".git").exists():
            p = p.parent
        if (p / ".git").exists():
            priority.append(str(p))

    ordered, seen = [], set()
    for r in priority:
        if r not in seen:
            seen.add(r)
            ordered.append((r, True))
    if not args.no_sweep:
        for r in find_repos(roots, depth):
            if r not in seen:
                seen.add(r)
                ordered.append((r, False))

    lines = []
    span = str(d_from) if d_from == d_to else "%s .. %s" % (d_from, d_to)
    lines.append("# Git commits -- work day %s" % span)
    lines.append("")
    lines.append("Window: %s -> %s (local)" % (since, until))
    lines.append("Authors matched: %s" % ", ".join(authors))
    lines.append("Repos scanned: %d (%d from agent sessions, %d swept from %s "
                 "at depth %d)"
                 % (len(ordered), len(priority), len(ordered) - len(priority),
                    " ".join(roots) if not args.no_sweep else "-", depth))
    lines.append("")

    total = 0
    body = []
    problems = []
    for repo, is_priority in ordered:
        commits, errs = commits_for(repo, authors, since, until)
        problems.extend("%s: %s" % (repo, e) for e in errs)
        if not commits:
            continue
        total += len(commits)
        tag = " (agent session repo)" if is_priority else ""
        body.append("## %s%s" % (repo, tag))
        for c in commits:
            stamp = c["date"][11:] if c["date"][:10] == str(d_from) \
                else c["date"]
            line = "- [%s] `%s` %s" % (stamp, c["hash"], c["subject"])
            if c["stat"]:
                line += "  -- %s" % c["stat"]
            body.append(line)
        body.append("")

    lines.append("Commits found: %d" % total)
    lines.append("")
    if problems:
        lines.append("## NOTES / gaps")
        for p in problems[:10]:
            lines.append("- %s" % p)
        lines.append("")
    if body:
        lines.extend(body)
    else:
        lines.append("_No commits by these authors in this window._")

    text = "\n".join(lines)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        sys.stderr.write("wrote %s (%d commits)\n" % (args.out, total))
    else:
        sys.stdout.write(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

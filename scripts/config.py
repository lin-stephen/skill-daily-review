#!/usr/bin/env python3
"""Shared configuration for the daily-review scripts.

Settings live in a YAML-frontmatter file, following the Claude Code plugin
settings convention but at user level rather than per project, because a daily
review spans the whole machine rather than one repo:

    ~/.claude/daily-review.local.md

Precedence, highest first: an explicit command-line flag, then this file, then
the built-in defaults. The file is read at run time, so edits take effect on
the next run -- no restart, because nothing here is a hook.

Lookup order for the file itself:
    1. --config PATH
    2. $DAILY_REVIEW_CONFIG
    3. ~/.claude/daily-review.local.md

CLI:
    config.py --show            print the effective settings and where each came from
    config.py --init            write a starter file with values detected from this machine
    config.py --init --force    overwrite an existing file
"""

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

DEFAULT_PATH = Path.home() / ".claude" / "daily-review.local.md"

DEFAULTS = {
    "day_start": "05:00",
    "repo_roots": ["~/projects", "~/src", "~/code", "~/work"],
    "repo_depth": 6,            # how deep under each root to look for .git
    "git_authors": [],          # empty -> fall back to global user.email / user.name
    "slack_user_id": "",        # empty -> read it from the Slack tool description
    "journal_lookback": 7,
    "sources": ["claude", "codex", "antigravity"],
}

_INT_RE = re.compile(r"^-?\d+$")


# ---------------------------------------------------------------------------
# A deliberately small YAML subset: scalars, inline lists, and block lists.
# ---------------------------------------------------------------------------
def _scalar(raw):
    v = raw.strip()
    if not v:
        return ""
    if len(v) >= 2 and v[0] in "\"'" and v[-1] == v[0]:
        return v[1:-1]
    if v.startswith("[") and v.endswith("]"):
        inner = v[1:-1].strip()
        return [_scalar(x) for x in inner.split(",")] if inner else []
    low = v.lower()
    if low in ("true", "yes"):
        return True
    if low in ("false", "no"):
        return False
    if _INT_RE.match(v):
        return int(v)
    return v


def parse_frontmatter(text):
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    out = {}
    pending_list_key = None
    for line in lines[1:]:
        if line.strip() == "---":
            break
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # a block-list item belonging to the key above it
        if line[:1] in (" ", "\t") and stripped.startswith("- ") and pending_list_key:
            out.setdefault(pending_list_key, [])
            if not isinstance(out[pending_list_key], list):
                out[pending_list_key] = []
            out[pending_list_key].append(_scalar(stripped[2:]))
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        if not value.strip():
            # bare "key:" means a block list follows
            out[key] = []
            pending_list_key = key
        else:
            out[key] = _scalar(value)
            pending_list_key = None
    return out


# ---------------------------------------------------------------------------
def config_path(explicit=None):
    if explicit:
        return Path(os.path.expanduser(explicit))
    env = os.environ.get("DAILY_REVIEW_CONFIG")
    if env:
        return Path(os.path.expanduser(env))
    return DEFAULT_PATH


def load(explicit=None):
    """Return (settings, origin) where origin maps each key to its source."""
    settings = dict(DEFAULTS)
    settings["repo_roots"] = list(DEFAULTS["repo_roots"])
    settings["sources"] = list(DEFAULTS["sources"])
    origin = {k: "default" for k in settings}

    path = config_path(explicit)
    settings["_path"] = str(path)
    settings["_exists"] = path.is_file()
    if not settings["_exists"]:
        return settings, origin

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        sys.stderr.write("daily-review: cannot read %s (%s); using defaults\n"
                         % (path, exc))
        return settings, origin

    for key, value in parse_frontmatter(raw).items():
        if key not in DEFAULTS:
            continue  # ignore unknown keys rather than failing the run
        if isinstance(DEFAULTS[key], list) and not isinstance(value, list):
            value = [value] if value != "" else []
        if value == "" and not isinstance(DEFAULTS[key], str):
            continue
        settings[key] = value
        origin[key] = str(path)
    return settings, origin


def expanded_roots(settings):
    return [os.path.expanduser(str(r)) for r in settings.get("repo_roots") or []]


def pick(flag_value, settings, key, flag_default=None):
    """A flag wins when the caller actually passed it."""
    if flag_value is not None and flag_value != flag_default:
        return flag_value
    return settings.get(key, DEFAULTS.get(key))


# ---------------------------------------------------------------------------
def _git_global(key):
    try:
        p = subprocess.run(["git", "config", "--global", key],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           timeout=10)
        return p.stdout.decode("utf-8", "replace").strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


SKIP_DIRS = {"Library", "Applications", "Movies", "Music", "Pictures",
             "Public", "Downloads", "node_modules", "go", "Parallels"}


def _contains_repo(root, max_depth=3, budget=1500):
    """True if any .git lives within max_depth levels of root.

    Bounded on both depth and directories visited so a huge tree cannot stall
    `--init`. scan_git.py sweeps to depth 4 from whatever root it is given,
    so a hit here means that root is worth configuring.
    """
    stack = [(root, 0)]
    seen = 0
    while stack:
        d, depth = stack.pop()
        seen += 1
        if seen > budget:
            return False
        try:
            entries = list(d.iterdir())
        except (OSError, PermissionError):
            continue
        for child in entries:
            if not child.is_dir():
                continue
            # test the child itself, so a repo at the depth limit still counts
            if (child / ".git").exists():
                return True
            if depth + 1 < max_depth and not child.name.startswith("."):
                if child.name in SKIP_DIRS:
                    continue
                stack.append((child, depth + 1))
    return False


def _detect_roots():
    """Find directories under $HOME that actually hold git checkouts.

    Detection rather than a hardcoded list, so nothing machine- or
    employer-specific needs to live in this repo.
    """
    home = Path.home()
    try:
        children = sorted(d for d in home.iterdir()
                          if d.is_dir() and not d.name.startswith(".")
                          and d.name not in SKIP_DIRS)
    except (OSError, PermissionError):
        children = []

    found = []
    for d in children[:40]:
        if (d / ".git").exists():
            continue  # a repo itself, not a container of repos
        if _contains_repo(d):
            found.append("~/" + d.name)
    return found[:8] or list(DEFAULTS["repo_roots"])


TEMPLATE = """---
# daily-review settings. Edit freely; read at run time, no restart needed.
# A command-line flag beats this file; this file beats the built-in defaults.

# Local time a work day begins. Work after midnight counts toward the day it
# started on, so 05:00 means the Nov 12 work day runs to Nov 13 05:00.
# Use "00:00" for plain calendar days.
day_start: "{day_start}"

# Where to sweep for git repos you authored commits in (depth 4).
repo_roots:
{repo_roots}

# How deep under each root to look for a .git directory. Raise it if your
# repos are nested further; each extra level costs a little scan time.
repo_depth: {repo_depth}

# Names/emails to match commits against. Empty = use global user.email and
# user.name. Add more if you commit under several identities.
git_authors:
{git_authors}

# Leave empty to read your Slack ID from the Slack tool description at run
# time, which stays correct if the account changes. Set it only to override.
slack_user_id: ""

# How many of your most recent journal entries to read for voice and for
# unclosed 可以更好的 items.
journal_lookback: {journal_lookback}

# Agent session sources to scan.
sources: [claude, codex, antigravity]
---

# daily-review

Settings for the `/daily-review` skill. This file is yours to edit at any time.

Anything not listed above stays at its default. Unknown keys are ignored
rather than treated as errors, so a stale key will not break a run.
"""


def render_template():
    authors = [a for a in (_git_global("user.email"), _git_global("user.name")) if a]
    return TEMPLATE.format(
        day_start=DEFAULTS["day_start"],
        repo_roots="\n".join("  - %s" % r for r in _detect_roots()),
        repo_depth=DEFAULTS["repo_depth"],
        git_authors=("\n".join("  - %s" % a for a in authors)
                     if authors else "  []"),
        journal_lookback=DEFAULTS["journal_lookback"],
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=None, help="settings file to use")
    ap.add_argument("--show", action="store_true",
                    help="print effective settings and where each came from")
    ap.add_argument("--init", action="store_true",
                    help="write a starter settings file")
    ap.add_argument("--force", action="store_true",
                    help="with --init, overwrite an existing file")
    args = ap.parse_args()

    if args.init:
        path = config_path(args.config)
        if path.exists() and not args.force:
            sys.stderr.write("%s already exists; pass --force to overwrite\n" % path)
            return 1
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_template(), encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:
            pass
        sys.stdout.write("wrote %s\n" % path)
        return 0

    settings, origin = load(args.config)
    path = settings["_path"]
    sys.stdout.write("settings file: %s%s\n"
                     % (path, "" if settings["_exists"] else "  (not present)"))
    if not settings["_exists"]:
        sys.stdout.write("  run `config.py --init` to create it\n")
    sys.stdout.write("\n")
    for key in DEFAULTS:
        value = settings[key]
        src = "default" if origin[key] == "default" else "file"
        sys.stdout.write("  %-18s %-44s [%s]\n" % (key + ":", repr(value), src))
    if not args.show:
        sys.stdout.write("\n(--show for the same output, --init to create the file)\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

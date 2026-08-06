"""The time dimension: what git already knows about every file.

One ``git log --numstat`` pass gives us, per file: how often it changes, how
violently, who changed it, and when it was last touched. Everything downstream —
hotspots, knowledge islands, fossils, coupling — is derived from this single
walk, because spawning git per file turns a two-second analysis into a
two-minute one.

Renames are followed. Git reports a rename once, at the commit that performed
it; walking newest-to-oldest and remembering ``old -> new`` lets us attribute
the whole prior life of a file to the name it has today.
"""

from __future__ import annotations

import re
import subprocess
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

#: Control characters are the only separators safe against paths, author names
#: and commit subjects. Git emits them itself via ``%x1e``/``%x1f`` so they never
#: have to travel through argv, which cannot carry a NUL.
_RECORD = "\x1e"
_FIELD = "\x1f"
_FORMAT = "%x1e%H%x1f%at%x1f%aN%x1f%s"

#: A commit touching more files than this is a bulk move, a licence header
#: sweep or a formatter run. Counting it as evidence of coupling would connect
#: everything to everything.
BULK_COMMIT_FILES = 40

_BRACE_RENAME = re.compile(r"^(.*)\{(.*) => (.*)\}(.*)$")


@dataclass
class Commit:
    sha: str
    timestamp: int
    author: str
    subject: str
    files: List[str] = field(default_factory=list)


@dataclass
class FileHistory:
    path: str
    commits: int = 0
    added: int = 0
    deleted: int = 0
    first_seen: int = 0
    last_seen: int = 0
    authors: Dict[str, int] = field(default_factory=dict)  #: author -> lines added
    author_commits: Dict[str, int] = field(default_factory=dict)

    @property
    def churn(self) -> int:
        return self.added + self.deleted

    @property
    def main_author(self) -> str:
        if not self.authors:
            return ""
        return max(self.authors.items(), key=lambda kv: (kv[1], kv[0]))[0]

    @property
    def ownership(self) -> float:
        """Share of added lines written by the single largest contributor."""
        total = sum(self.authors.values())
        if not total:
            return 0.0
        return max(self.authors.values()) / total


@dataclass
class History:
    commits: List[Commit] = field(default_factory=list)
    files: Dict[str, FileHistory] = field(default_factory=dict)
    authors: Dict[str, int] = field(default_factory=dict)  #: author -> commit count
    head_time: int = 0
    first_time: int = 0
    truncated: bool = False

    @property
    def available(self) -> bool:
        return bool(self.commits)


def _split_rename(path: str) -> Optional[Tuple[str, str]]:
    """Decode git's compressed rename notation into ``(old, new)``."""
    if " => " not in path:
        return None
    match = _BRACE_RENAME.match(path)
    if match:
        prefix, old_mid, new_mid, suffix = match.groups()
        old = f"{prefix}{old_mid}{suffix}".replace("//", "/")
        new = f"{prefix}{new_mid}{suffix}".replace("//", "/")
        return old, new
    old, _, new = path.partition(" => ")
    return old.strip(), new.strip()


def _run_git_log(root: str, since: Optional[str], max_commits: Optional[int]) -> Optional[str]:
    cmd = [
        "git", "-C", root, "log", "--no-merges", "-M", "--numstat",
        "--date=raw", f"--format={_FORMAT}",
    ]
    if since:
        cmd.append(f"--since={since}")
    if max_commits:
        cmd.append(f"--max-count={max_commits}")
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout


def parse_log(raw: str, known: Optional[Set[str]] = None) -> History:
    """Parse ``git log --numstat`` output into a :class:`History`.

    ``known`` restricts attention to paths that exist in the working tree today;
    history for deleted files is real but not actionable.
    """
    history = History()
    #: historical path -> the path it is known by today
    renamed_to: Dict[str, str] = {}

    for chunk in raw.split(_RECORD):
        if not chunk.strip():
            continue
        header, _, body = chunk.partition("\n")
        parts = header.split(_FIELD)
        if len(parts) < 3:
            continue
        sha, stamp, author = parts[0], parts[1], parts[2]
        subject = parts[3] if len(parts) > 3 else ""
        try:
            timestamp = int(stamp)
        except ValueError:
            continue

        commit = Commit(sha=sha, timestamp=timestamp, author=author, subject=subject)

        for line in body.split("\n"):
            if not line.strip():
                continue
            fields = line.split("\t")
            if len(fields) < 3:
                continue
            add_s, del_s, raw_path = fields[0], fields[1], "\t".join(fields[2:])

            rename = _split_rename(raw_path)
            if rename:
                old, new = rename
                current = renamed_to.get(new, new)
                renamed_to[old] = current
                path = current
            else:
                path = renamed_to.get(raw_path, raw_path)

            if known is not None and path not in known:
                continue

            added = int(add_s) if add_s.isdigit() else 0
            deleted = int(del_s) if del_s.isdigit() else 0

            entry = history.files.get(path)
            if entry is None:
                entry = FileHistory(path=path)
                history.files[path] = entry
            entry.commits += 1
            entry.added += added
            entry.deleted += deleted
            entry.authors[author] = entry.authors.get(author, 0) + added
            entry.author_commits[author] = entry.author_commits.get(author, 0) + 1
            entry.last_seen = max(entry.last_seen, timestamp)
            entry.first_seen = timestamp if entry.first_seen == 0 else min(entry.first_seen, timestamp)

            commit.files.append(path)

        history.authors[author] = history.authors.get(author, 0) + 1
        history.commits.append(commit)

    if history.commits:
        stamps = [c.timestamp for c in history.commits]
        history.head_time = max(stamps)
        history.first_time = min(stamps)
    return history


def read_history(
    root: str,
    known: Optional[Set[str]] = None,
    since: Optional[str] = None,
    max_commits: Optional[int] = None,
) -> History:
    """Read history for ``root``; an empty :class:`History` if git says no."""
    raw = _run_git_log(root, since, max_commits)
    if raw is None:
        return History()
    history = parse_log(raw, known)
    history.truncated = bool(max_commits and len(history.commits) >= max_commits)
    return history


def author_totals(history: History) -> List[Tuple[str, int, int]]:
    """``(author, commits, files_owned)`` sorted by commits, descending."""
    owned: Dict[str, int] = defaultdict(int)
    for entry in history.files.values():
        main = entry.main_author
        if main:
            owned[main] += 1
    rows = [(name, count, owned.get(name, 0)) for name, count in history.authors.items()]
    rows.sort(key=lambda row: (-row[1], row[0]))
    return rows

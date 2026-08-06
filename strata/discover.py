"""Deciding which files count.

Inside a git repository the answer is easy and exactly right: ask git. Its index
already encodes every ``.gitignore`` rule in the tree, so ``git ls-files`` gives
us the authored files and nothing else. Outside one we fall back to walking the
tree with a conservative deny-list.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .langs import IGNORED_DIRS, is_generated, language_for

#: Files above this size are data, not something a person maintains.
MAX_FILE_BYTES = 2_000_000


@dataclass
class SourceFile:
    path: str  #: Repo-relative, forward-slashed.
    size: int
    text: str

    @property
    def directory(self) -> str:
        head, _, _ = self.path.rpartition("/")
        return head

    @property
    def name(self) -> str:
        return self.path.rpartition("/")[2]


def git_root(start: str) -> Optional[str]:
    """Absolute path of the enclosing work tree, or ``None``."""
    try:
        out = subprocess.run(
            ["git", "-C", start, "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    root = out.stdout.strip()
    return root or None


def _tracked_paths(root: str) -> Optional[List[str]]:
    try:
        out = subprocess.run(
            ["git", "-C", root, "ls-files", "-z", "--cached", "--exclude-standard"],
            capture_output=True, text=True, timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return [p for p in out.stdout.split("\0") if p]


def _walked_paths(root: str) -> List[str]:
    found: List[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames if d not in IGNORED_DIRS and not d.startswith(".")
        )
        rel_dir = os.path.relpath(dirpath, root)
        prefix = "" if rel_dir == "." else rel_dir.replace(os.sep, "/") + "/"
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            found.append(prefix + name)
    return found


def _read(root: str, rel: str) -> Optional[Tuple[int, str]]:
    full = os.path.join(root, rel)
    try:
        size = os.path.getsize(full)
    except OSError:
        return None
    if size > MAX_FILE_BYTES:
        return None
    try:
        with open(full, "r", encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except (OSError, ValueError):
        return None
    if "\0" in text[:4096]:
        return None  # binary
    return size, text


def discover(
    root: str,
    include_generated: bool = False,
    extra_excludes: Tuple[str, ...] = (),
) -> Tuple[List[SourceFile], bool]:
    """Collect readable, authored text files under ``root``.

    Returns the files and whether the listing came from git (which means
    ``.gitignore`` was honoured exactly).
    """
    root = os.path.abspath(root)
    paths = _tracked_paths(root)
    # An empty listing means a repository whose first commit has not happened
    # yet, or one where everything is still unstaged. Walking the tree is the
    # only useful answer; reporting zero files would be technically true and
    # completely unhelpful.
    from_git = bool(paths)
    if not paths:
        paths = _walked_paths(root)

    files: List[SourceFile] = []
    for rel in paths:
        rel = rel.replace(os.sep, "/")
        parts = rel.split("/")
        if any(part in IGNORED_DIRS for part in parts[:-1]):
            continue
        if extra_excludes and any(fragment in rel for fragment in extra_excludes):
            continue
        if not include_generated and is_generated(rel):
            continue
        if language_for(rel) is None:
            continue
        read = _read(root, rel)
        if read is None:
            continue
        size, text = read
        files.append(SourceFile(path=rel, size=size, text=text))

    files.sort(key=lambda f: f.path)
    return files, from_git

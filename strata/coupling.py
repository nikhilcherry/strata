"""Files that change together.

The most useful thing in a repository's history is also the least visible: two
files that are always edited in the same commit, with no import between them.
Nothing in the code says they are related. Only the log does. That pair is where
somebody will forget the second edit.

Coupling degree is symmetric and normalised against the *rarer* of the two
files::

    degree(a, b) = co_changes(a, b) / min(changes(a), changes(b))

so a helper touched twice, both times alongside its caller, reads as fully
coupled — which it is — while a file touched 400 times that happens to overlap
30 times with another does not.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from itertools import combinations
from math import log
from typing import Dict, List, Sequence, Set, Tuple

from .history import BULK_COMMIT_FILES, Commit


@dataclass
class CoupledPair:
    left: str
    right: str
    co_changes: int
    degree: float
    #: True when neither file imports the other — the interesting case.
    hidden: bool = False

    @property
    def score(self) -> float:
        """Rank by strength, but never let a 2-of-2 outrank a 30-of-33."""
        return self.degree * log(self.co_changes + 1)


def temporal_coupling(
    commits: Sequence[Commit],
    file_changes: Dict[str, int],
    min_co_changes: int = 4,
    min_degree: float = 0.4,
    bulk_threshold: int = BULK_COMMIT_FILES,
    limit: int = 400,
) -> List[CoupledPair]:
    """Rank co-changing file pairs.

    ``file_changes`` maps a path to its total commit count, and doubles as the
    set of paths worth considering — pass only files that still exist.
    """
    pair_counts: Dict[Tuple[str, str], int] = defaultdict(int)

    for commit in commits:
        touched = sorted({p for p in commit.files if p in file_changes})
        if len(touched) < 2 or len(touched) > bulk_threshold:
            continue
        for left, right in combinations(touched, 2):
            pair_counts[(left, right)] += 1

    pairs: List[CoupledPair] = []
    for (left, right), count in pair_counts.items():
        if count < min_co_changes:
            continue
        rarer = min(file_changes[left], file_changes[right])
        if rarer <= 0:
            continue
        degree = count / rarer
        if degree < min_degree:
            continue
        pairs.append(CoupledPair(left=left, right=right, co_changes=count, degree=min(degree, 1.0)))

    pairs.sort(key=lambda p: (-p.score, p.left, p.right))
    return pairs[:limit]


def mark_hidden(pairs: Sequence[CoupledPair], edges: Dict[str, Set[str]]) -> None:
    """Flag pairs that share no import edge in either direction, in place."""
    for pair in pairs:
        linked = pair.right in edges.get(pair.left, ()) or pair.left in edges.get(pair.right, ())
        pair.hidden = not linked

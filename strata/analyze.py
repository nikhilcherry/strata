"""Where structure meets time.

Complexity alone tells you which files are hard. History alone tells you which
files are busy. Neither is actionable: the hard file nobody touches is fine, and
the busy file that is three lines long is fine. The product is not fine, and
that product is what this module computes.

Everything a renderer needs comes out of :func:`analyse` as one ``Report``.
"""

from __future__ import annotations

import posixpath
import time
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Sequence, Set, Tuple

from .complexity import analyse_text
from .coupling import CoupledPair, mark_hidden, temporal_coupling
from .discover import discover, git_root
from .graph import cycle_path, fan_in_counts, strongly_connected
from .history import History, author_totals, read_history
from .imports import build_graph, directory_graph
from .langs import is_code, is_test, language_for

DAY = 86400.0
#: A commit this many days old counts for half as much as one today.
DEFAULT_HALF_LIFE = 180.0
#: Untouched for this long, and imported by nothing, is a fossil.
FOSSIL_DAYS = 365
#: Nesting this deep is worth naming out loud.
DEEP_NESTING = 7

_ENTRYPOINT_STEMS = {
    "main", "index", "app", "cli", "__main__", "setup", "conf", "settings",
    "server", "manage", "lib", "mod", "run", "bootstrap",
}


@dataclass
class FileRecord:
    path: str
    lang: str
    lines: int = 0
    code_lines: int = 0
    comment_lines: int = 0
    complexity: float = 0.0
    max_indent: int = 0
    longest_line: int = 0
    commits: int = 0
    added: int = 0
    deleted: int = 0
    authors: int = 0
    main_author: str = ""
    ownership: float = 0.0
    first_seen: int = 0
    last_seen: int = 0
    age_days: float = 0.0
    idle_days: float = 0.0
    fan_in: int = 0
    fan_out: int = 0
    is_test: bool = False
    is_code: bool = True
    #: Recency-weighted change activity.
    activity: float = 0.0
    #: ``complexity x activity``, normalised to ``0..1`` across the repo.
    heat: float = 0.0

    @property
    def churn(self) -> int:
        return self.added + self.deleted

    @property
    def directory(self) -> str:
        return posixpath.dirname(self.path)


@dataclass
class Finding:
    kind: str
    severity: str  #: "high" | "medium" | "low"
    title: str
    detail: str
    paths: List[str] = field(default_factory=list)


@dataclass
class Report:
    root: str
    name: str
    generated_at: int
    files: List[FileRecord] = field(default_factory=list)
    coupling: List[CoupledPair] = field(default_factory=list)
    cycles: List[List[str]] = field(default_factory=list)
    cycle_paths: List[List[str]] = field(default_factory=list)
    directory_cycles: List[List[str]] = field(default_factory=list)
    findings: List[Finding] = field(default_factory=list)
    authors: List[Tuple[str, int, int]] = field(default_factory=list)
    externals: List[Tuple[str, int]] = field(default_factory=list)
    languages: List[Tuple[str, int, int]] = field(default_factory=list)
    edges: Dict[str, List[str]] = field(default_factory=dict)
    #: False when the directory is not a git repository — rankings fall back
    #: to structure alone.
    has_history: bool = False
    commit_count: int = 0
    span_days: float = 0.0
    from_git_index: bool = True
    half_life: float = DEFAULT_HALF_LIFE

    @property
    def code_files(self) -> List[FileRecord]:
        return [f for f in self.files if f.is_code]

    @property
    def total_lines(self) -> int:
        return sum(f.lines for f in self.files)

    def hotspots(self, limit: int = 20, include_tests: bool = False) -> List[FileRecord]:
        ranked = [
            f for f in self.files
            if f.is_code and f.heat > 0 and (include_tests or not f.is_test)
        ]
        ranked.sort(key=lambda f: (-f.heat, f.path))
        return ranked[:limit]

    def fossils(self, limit: int = 20) -> List[FileRecord]:
        candidates = [
            f for f in self.files
            if f.is_code and not f.is_test and f.fan_in == 0
            and f.idle_days >= FOSSIL_DAYS and f.code_lines >= 10
            and posixpath.splitext(f.path.rpartition("/")[2])[0] not in _ENTRYPOINT_STEMS
        ]
        candidates.sort(key=lambda f: (-f.idle_days, -f.lines))
        return candidates[:limit]

    def knowledge_islands(self, limit: int = 20) -> List[FileRecord]:
        candidates = [
            f for f in self.files
            if f.is_code and not f.is_test and f.authors == 1 and f.commits >= 2 and f.code_lines >= 40
        ]
        candidates.sort(key=lambda f: (-f.complexity, f.path))
        return candidates[:limit]


def _weighted_activity(history: History, path: str, now: float, half_life: float) -> float:
    """Commit count for ``path``, decayed by how long ago the work happened.

    A cheap stand-in for summing the decay of every individual commit: blend the
    decay at the most recent touch with the decay at the midpoint of the file's
    life. Walking every commit per file is O(commits x files) and moves no
    ranking we have been able to observe.
    """
    entry = history.files.get(path)
    if entry is None:
        return 0.0
    recent_age = (now - entry.last_seen) / DAY if entry.last_seen else 3650.0
    mid_age = (now - (entry.first_seen + entry.last_seen) / 2) / DAY if entry.first_seen else 3650.0
    recency = 0.5 ** (max(recent_age, 0.0) / half_life)
    spread = 0.5 ** (max(mid_age, 0.0) / half_life)
    return entry.commits * (0.65 * recency + 0.35 * spread)


def _peak(values: Sequence[float]) -> float:
    top = max(values) if values else 0.0
    return top if top > 0 else 1.0


def analyse(
    path: str,
    since: Optional[str] = None,
    max_commits: Optional[int] = None,
    half_life: float = DEFAULT_HALF_LIFE,
    include_generated: bool = False,
    exclude: Tuple[str, ...] = (),
    no_history: bool = False,
    min_co_changes: int = 4,
) -> Report:
    """Analyse the repository (or plain directory) at ``path``."""
    root = git_root(path) or path
    files, from_index = discover(root, include_generated=include_generated, extra_excludes=exclude)
    known = {f.path for f in files}

    history = History() if no_history else read_history(root, known, since, max_commits)
    now = float(max(int(time.time()), history.head_time))

    graph = build_graph(files)
    fan_in = fan_in_counts(graph.edges)

    records: List[FileRecord] = []
    for source in files:
        lang = language_for(source.path)
        stats = analyse_text(source.text, lang)
        entry = history.files.get(source.path)

        record = FileRecord(
            path=source.path,
            lang=lang.name if lang else "?",
            lines=stats.lines,
            code_lines=stats.code_lines,
            comment_lines=stats.comment_lines,
            complexity=stats.complexity,
            max_indent=stats.max_indent,
            longest_line=stats.longest_line,
            fan_in=fan_in.get(source.path, 0),
            fan_out=graph.fan_out(source.path),
            is_test=is_test(source.path),
            is_code=is_code(source.path),
        )
        if entry is not None:
            record.commits = entry.commits
            record.added = entry.added
            record.deleted = entry.deleted
            record.authors = len(entry.authors)
            record.main_author = entry.main_author
            record.ownership = entry.ownership
            record.first_seen = entry.first_seen
            record.last_seen = entry.last_seen
            record.age_days = (now - entry.first_seen) / DAY if entry.first_seen else 0.0
            record.idle_days = (now - entry.last_seen) / DAY if entry.last_seen else 0.0
            record.activity = _weighted_activity(history, source.path, now, half_life)
        records.append(record)

    _score_heat(records, history.available)

    report = Report(
        root=root,
        name=posixpath.basename(root.rstrip("/")) or root,
        generated_at=int(now),
        files=records,
        has_history=history.available,
        commit_count=len(history.commits),
        from_git_index=from_index,
        half_life=half_life,
    )
    if history.available:
        report.span_days = (history.head_time - history.first_time) / DAY
        report.authors = author_totals(history)
        changes = {r.path: r.commits for r in records if r.commits > 0}
        report.coupling = temporal_coupling(history.commits, changes, min_co_changes=min_co_changes)
        mark_hidden(report.coupling, graph.edges)

    report.cycles = strongly_connected(graph.edges, known)[:40]
    report.cycle_paths = [cycle_path(graph.edges, component) for component in report.cycles]
    report.directory_cycles = strongly_connected(directory_graph(graph))[:20]
    report.externals = _external_totals(graph.external)
    report.languages = _language_totals(records)
    report.edges = {src: sorted(dsts) for src, dsts in graph.edges.items() if dsts}
    report.findings = derive_findings(report)
    return report


def _score_heat(records: Sequence[FileRecord], has_history: bool) -> None:
    """Assign every record its normalised ``complexity x activity``."""
    ranked = [r for r in records if r.is_code and not r.is_test]
    if not ranked:
        return
    max_complexity = _peak([r.complexity for r in ranked])
    max_activity = _peak([r.activity for r in ranked])

    raw: List[Tuple[FileRecord, float]] = []
    for record in records:
        if not record.is_code:
            continue
        complexity_n = record.complexity / max_complexity
        # No history: rank on shape alone. The report says so in its header.
        activity_n = record.activity / max_activity if has_history else 1.0
        raw.append((record, complexity_n * activity_n))

    top = _peak([value for _, value in raw])
    for record, value in raw:
        record.heat = round(value / top, 6)


def _external_totals(external: Dict[str, Set[str]], limit: int = 40) -> List[Tuple[str, int]]:
    counts: Dict[str, int] = {}
    for names in external.values():
        for name in names:
            counts[name] = counts.get(name, 0) + 1
    rows = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return rows[:limit]


def _language_totals(records: Sequence[FileRecord]) -> List[Tuple[str, int, int]]:
    counts: Dict[str, List[int]] = {}
    for record in records:
        bucket = counts.setdefault(record.lang, [0, 0])
        bucket[0] += 1
        bucket[1] += record.lines
    rows = [(name, files, lines) for name, (files, lines) in counts.items()]
    rows.sort(key=lambda row: (-row[2], row[0]))
    return rows


def derive_findings(report: Report) -> List[Finding]:
    """Turn measurements into things worth doing something about."""
    findings: List[Finding] = []
    hotspots = report.hotspots(limit=5)

    if hotspots:
        top = hotspots[0]
        shape = f"{top.code_lines} lines of code, nesting {top.max_indent} deep"
        if report.has_history:
            severity = "high" if top.heat > 0.5 and top.commits >= 5 else "medium"
            detail = (
                f"{shape}, changed in {top.commits} commits by {top.authors} "
                f"author{'s' if top.authors != 1 else ''}. "
                "It is both the hardest and the busiest file here."
            )
        else:
            severity = "medium"
            detail = (
                f"{shape}. There is no commit history to weigh this against, so "
                "this is the most tangled file, not necessarily the riskiest one."
            )
        findings.append(
            Finding(
                kind="hotspot",
                severity=severity,
                title=f"{top.path} is the centre of gravity",
                detail=detail,
                paths=[f.path for f in hotspots],
            )
        )

    hidden = [p for p in report.coupling if p.hidden][:5]
    if hidden:
        worst = hidden[0]
        findings.append(
            Finding(
                kind="hidden-coupling",
                severity="high" if worst.degree >= 0.7 and worst.co_changes >= 6 else "medium",
                title="Files change together but nothing connects them",
                detail=(
                    f"{worst.left} and {worst.right} changed together in "
                    f"{worst.co_changes} commits ({worst.degree:.0%} of the time), "
                    "with no import in either direction. Nothing in the code will "
                    "remind you to make the second edit."
                ),
                paths=[f"{p.left} <-> {p.right}" for p in hidden],
            )
        )

    if report.cycles:
        biggest = report.cycle_paths[0] if report.cycle_paths else report.cycles[0]
        findings.append(
            Finding(
                kind="cycle",
                severity="high" if len(report.cycles[0]) > 4 else "medium",
                title=f"{len(report.cycles)} import cycle{'s' if len(report.cycles) != 1 else ''}",
                detail=" -> ".join(biggest),
                paths=[" -> ".join(path) for path in report.cycle_paths[:5]],
            )
        )

    islands = report.knowledge_islands(limit=5)
    if islands and len(report.authors) > 1:
        total_islands = len(report.knowledge_islands(limit=10_000))
        findings.append(
            Finding(
                kind="knowledge-island",
                severity="medium",
                title=f"{total_islands} files have exactly one author",
                detail=(
                    f"{islands[0].path} has only ever been touched by "
                    f"{islands[0].main_author or 'one person'}. If they are away, so is this code."
                ),
                paths=[f.path for f in islands],
            )
        )

    fossils = report.fossils(limit=5)
    if fossils:
        oldest = fossils[0]
        findings.append(
            Finding(
                kind="fossil",
                severity="low",
                title=f"{len(report.fossils(limit=10_000))} files look abandoned",
                detail=(
                    f"{oldest.path} has not changed in {oldest.idle_days / 365:.1f} years "
                    "and nothing in the repository imports it."
                ),
                paths=[f.path for f in fossils],
            )
        )

    deep = sorted(
        (f for f in report.files if f.is_code and f.max_indent >= DEEP_NESTING),
        key=lambda f: (-f.max_indent, f.path),
    )[:5]
    if deep:
        findings.append(
            Finding(
                kind="deep-nesting",
                severity="low",
                title=f"{deep[0].max_indent} levels of nesting in {deep[0].path}",
                detail="Deeply nested code is where off-by-one and early-return bugs hide.",
                paths=[f"{f.path} ({f.max_indent} deep)" for f in deep],
            )
        )

    order = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda f: order.get(f.severity, 3))
    return findings


def to_dict(report: Report) -> dict:
    """A JSON-serialisable view of the whole report."""
    files = []
    for record in report.files:
        row = asdict(record)
        row["churn"] = record.churn
        files.append(row)

    pairs = []
    for pair in report.coupling:
        row = asdict(pair)
        row["score"] = round(pair.score, 4)
        pairs.append(row)

    return {
        "root": report.root,
        "name": report.name,
        "generated_at": report.generated_at,
        "has_history": report.has_history,
        "commit_count": report.commit_count,
        "span_days": round(report.span_days, 2),
        "half_life_days": report.half_life,
        "languages": [{"name": n, "files": f, "lines": l} for n, f, l in report.languages],
        "authors": [{"name": n, "commits": c, "files_owned": o} for n, c, o in report.authors],
        "externals": [{"name": n, "used_by": c} for n, c in report.externals],
        "files": files,
        "coupling": pairs,
        "cycles": [{"files": c, "path": p} for c, p in zip(report.cycles, report.cycle_paths)],
        "directory_cycles": report.directory_cycles,
        "findings": [asdict(f) for f in report.findings],
    }

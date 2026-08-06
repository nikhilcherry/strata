"""The terminal report.

Most of the time you do not want to open a browser — you want to know, in the
two seconds after typing the command, which three files deserve your attention.
That is what this prints.

Colour is disabled automatically when stdout is not a terminal, when ``NO_COLOR``
is set, or when ``TERM=dumb``, so piping the output into a file gives clean text.
"""

from __future__ import annotations

import os
import shutil
import sys
from typing import List, Optional, Sequence

from .analyze import Report
from .render_html import _friendly_root

_HEAT_RAMP = (
    (0.75, "\033[38;5;209m"),
    (0.45, "\033[38;5;179m"),
    (0.20, "\033[38;5;103m"),
    (0.00, "\033[38;5;60m"),
)
_BLOCKS = " ▁▂▃▄▅▆▇█"


class Style:
    """ANSI codes, or empty strings when colour is off."""

    def __init__(self, enabled: bool) -> None:
        self.on = enabled

    def _wrap(self, code: str, text: str) -> str:
        return f"{code}{text}\033[0m" if self.on else text

    def dim(self, text: str) -> str:
        return self._wrap("\033[2m", text)

    def bold(self, text: str) -> str:
        return self._wrap("\033[1m", text)

    def accent(self, text: str) -> str:
        return self._wrap("\033[38;5;214m", text)

    def high(self, text: str) -> str:
        return self._wrap("\033[38;5;203m", text)

    def med(self, text: str) -> str:
        return self._wrap("\033[38;5;179m", text)

    def low(self, text: str) -> str:
        return self._wrap("\033[38;5;110m", text)

    def heat(self, value: float, text: str) -> str:
        for threshold, code in _HEAT_RAMP:
            if value >= threshold:
                return self._wrap(code, text)
        return text

    def severity(self, level: str, text: str) -> str:
        return {"high": self.high, "medium": self.med}.get(level, self.low)(text)


def colour_enabled(force: Optional[bool] = None) -> bool:
    if force is not None:
        return force
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    return sys.stdout.isatty()


def width(default: int = 92) -> int:
    return min(shutil.get_terminal_size((default, 24)).columns, 110)


def _elide(path: str, limit: int) -> str:
    if limit < 4 or len(path) <= limit:
        return path
    return "…" + path[-(limit - 1):]


def _days(value: float) -> str:
    if value < 1:
        return "today"
    if value < 45:
        return f"{value:.0f}d"
    if value < 730:
        return f"{value / 30.4:.0f}mo"
    return f"{value / 365:.1f}y"


def _spark(values: Sequence[float]) -> str:
    if not values:
        return ""
    top = max(values) or 1.0
    return "".join(_BLOCKS[min(8, int(v / top * 8))] for v in values)


def _wrap_text(text: str, limit: int) -> List[str]:
    lines: List[str] = []
    current = ""
    for word in text.split():
        if current and len(current) + 1 + len(word) > limit:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    return lines


def _rule(style: Style, title: str, total: int) -> str:
    label = f"── {title} "
    return style.dim(label + "─" * max(0, total - len(label)))


def _header(report: Report, style: Style) -> str:
    lines = [
        style.accent("strata") + style.dim("  code archaeology"),
        "",
        f"  {style.bold(report.name)}  {style.dim(_friendly_root(report.root))}",
    ]
    facts = [f"{len(report.files)} files", f"{report.total_lines:,} lines"]
    if report.has_history:
        facts.append(f"{report.commit_count:,} commits")
        if report.span_days > 40:
            facts.append(f"{report.span_days / 30.4:.0f} months")
        facts.append(f"{len(report.authors)} author{'s' if len(report.authors) != 1 else ''}")
    else:
        facts.append("no git history")
    lines.append("  " + style.dim(" · ".join(facts)))
    lines.append("")
    return "\n".join(lines) + "\n"


def _hotspot_table(report: Report, rows, style: Style, total: int) -> str:
    path_width = max(24, total - 34)
    out = []
    for record in rows:
        filled = max(1, round(record.heat * 12))
        bar = style.heat(record.heat, "█" * filled) + style.dim("·" * (12 - filled))
        edits = f"{record.commits:>4}" if report.has_history else "   —"
        out.append(
            f"   {bar} {_elide(record.path, path_width).ljust(path_width)} "
            f"{style.dim(str(record.lines).rjust(5) + 'L')} {style.dim(edits + ' edits')}\n"
        )
    return "".join(out)


def _footer(report: Report, style: Style) -> str:
    if not report.has_history:
        return style.dim("  Not a git repository — rankings use structure only.\n")
    activity = _spark([f.activity for f in report.hotspots(limit=24)])
    return style.dim(f"  half-life {report.half_life:.0f}d   activity {activity}\n")


def print_report(report: Report, style: Style, top: int = 10, out=None) -> None:
    """The full terminal dig: header, findings, hotspots, coupling, fossils."""
    write = (out or sys.stdout).write
    total = width()

    write(_header(report, style))
    write("\n")

    if report.findings:
        write(_rule(style, "the dig report", total) + "\n\n")
        for finding in report.findings:
            marker = style.severity(finding.severity, "▌")
            write(f" {marker} {style.bold(finding.title)}\n")
            for line in _wrap_text(finding.detail, total - 6):
                write(f"   {style.dim(line)}\n")
            for path in finding.paths[:4]:
                write(f"     {style.dim('·')} {_elide(path, total - 8)}\n")
            write("\n")

    hotspots = report.hotspots(limit=top)
    if hotspots:
        write(_rule(style, "hotspots", total) + "\n")
        note = (
            "complexity × recent change" if report.has_history
            else "complexity only — no git history here"
        )
        write(f"   {style.dim(note)}\n\n")
        write(_hotspot_table(report, hotspots, style, total))
        write("\n")

    hidden = [p for p in report.coupling if p.hidden][:6]
    if hidden:
        write(_rule(style, "change coupling with no import", total) + "\n\n")
        for pair in hidden:
            filled = max(1, round(pair.degree * 10))
            bar = style.accent("█" * filled) + style.dim("·" * (10 - filled))
            write(f"   {bar} {pair.degree:>4.0%} {style.dim(f'×{pair.co_changes}')}\n")
            write(f"     {_elide(pair.left, total - 8)}\n")
            write(f"     {_elide(pair.right, total - 8)}\n\n")

    if report.cycles:
        write(_rule(style, "import cycles", total) + "\n\n")
        for path in report.cycle_paths[:4]:
            write("   " + style.dim(" → ").join(_elide(p, 40) for p in path) + "\n")
        if len(report.cycles) > 4:
            write(style.dim(f"   … and {len(report.cycles) - 4} more\n"))
        write("\n")

    fossils = report.fossils(limit=6)
    if fossils:
        write(_rule(style, "fossils", total) + "\n")
        write(f"   {style.dim('idle, and imported by nothing')}\n\n")
        for record in fossils:
            write(
                f"   {style.dim(_days(record.idle_days).rjust(6))}  "
                f"{_elide(record.path, total - 24).ljust(max(0, total - 24))} "
                f"{style.dim(f'{record.lines} lines')}\n"
            )
        write("\n")

    write(_footer(report, style))


def print_hotspots(report: Report, style: Style, top: int, out=None) -> None:
    write = (out or sys.stdout).write
    rows = report.hotspots(limit=top)
    if not rows:
        write("No code files found.\n")
        return
    write(_hotspot_table(report, rows, style, width()))


def print_coupling(report: Report, style: Style, top: int, hidden_only: bool, out=None) -> None:
    write = (out or sys.stdout).write
    pairs = [p for p in report.coupling if p.hidden or not hidden_only][:top]
    if not pairs:
        write("No co-changing pairs above the threshold.\n")
        return
    for pair in pairs:
        tag = style.high(" no import") if pair.hidden else style.dim(" imported")
        write(f"  {pair.degree:>4.0%} {style.dim(f'×{pair.co_changes}')}{tag}\n")
        write(f"       {pair.left}\n       {pair.right}\n\n")


def print_fossils(report: Report, style: Style, top: int, out=None) -> None:
    write = (out or sys.stdout).write
    rows = report.fossils(limit=top)
    if not rows:
        write("Nothing looks abandoned.\n")
        return
    for record in rows:
        write(
            f"  {style.dim(_days(record.idle_days).rjust(6))}  {record.path} "
            f"{style.dim(f'{record.lines} lines')}\n"
        )


def print_cycles(report: Report, style: Style, out=None) -> None:
    write = (out or sys.stdout).write
    if not report.cycles:
        write("No import cycles.\n")
        return
    for path in report.cycle_paths:
        write("  " + style.dim(" → ").join(path) + "\n")


def print_knowledge(report: Report, style: Style, out=None) -> None:
    write = (out or sys.stdout).write
    if not report.has_history:
        write("No git history, so no authorship to report.\n")
        return
    write(style.dim("  author                      commits   files owned\n"))
    for name, commits, owned in report.authors[:15]:
        write(f"  {name[:26].ljust(26)}  {str(commits).rjust(7)}   {str(owned).rjust(11)}\n")
    islands = report.knowledge_islands(limit=10)
    if islands:
        write("\n" + style.dim("  single-author files\n"))
        for record in islands:
            write(f"    {record.path} {style.dim(record.main_author)}\n")

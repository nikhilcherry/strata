"""Language-agnostic complexity, measured from indentation.

Counting branches properly needs a parser per language, and a parser per
language is a project that never ends. Indentation is the cheap proxy: nesting
is what makes code hard to hold in your head, and every language in common use
indents its nesting.

The metric is the *sum of indentation levels over logical lines*. A flat file
of a hundred statements scores 0. A hundred-line file with a loop inside a
conditional inside a method scores several hundred. That ordering is what the
hotspot ranking needs; the absolute number is never shown to the user.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .langs import Language

TAB_WIDTH = 4
#: Lines longer than this are almost certainly minified or data, not source.
_MINIFIED_LINE = 2000


@dataclass
class ComplexityStats:
    lines: int = 0
    #: Lines that are neither blank nor comment-only.
    code_lines: int = 0
    comment_lines: int = 0
    blank_lines: int = 0
    #: Sum of indentation levels across code lines.
    total_indent: int = 0
    max_indent: int = 0
    #: Standard deviation of indentation — a spread-out shape, not just a deep one.
    indent_stdev: float = 0.0
    #: Longest single line, a decent smell for "this line does too much".
    longest_line: int = 0

    @property
    def mean_indent(self) -> float:
        return self.total_indent / self.code_lines if self.code_lines else 0.0

    @property
    def complexity(self) -> float:
        """The headline number: how much nesting this file asks you to hold."""
        return float(self.total_indent)


def _leading_width(line: str) -> Optional[int]:
    """Visual width of the leading whitespace, or ``None`` for a blank line."""
    width = 0
    for ch in line:
        if ch == " ":
            width += 1
        elif ch == "\t":
            width += TAB_WIDTH - (width % TAB_WIDTH)
        else:
            return width
    return None


#: Indentation conventions worth considering, widest first. A "unit" of 1 is
#: not a convention anybody writes — it is what you infer when the evidence is
#: noise, and it silently turns columns into levels.
_UNIT_CANDIDATES = (8, 6, 4, 3, 2)
#: Share of indented lines a candidate step must land on exactly to be believed.
_UNIT_COVERAGE = 0.75
#: A width has to carry at least this share of the indented lines before it is
#: taken as evidence of the first indent level rather than a wrapped line.
_WIDTH_SUPPORT = 0.10


def detect_indent_unit(widths: List[int]) -> int:
    """Infer one indentation step from the widths actually observed.

    Scores each plausible step by the share of indented lines whose width it
    divides exactly, and takes the widest step that still explains most of the
    file.

    Measuring the gaps between distinct widths instead — the obvious approach —
    is wrecked by continuation lines. A few statements wrapped to align under an
    open bracket sit at arbitrary columns, contributing gaps of 1 that outvote
    the real steps and collapse the unit to 1. Every column then reads as a
    nesting level, which inflates both the reported depth and the complexity
    score the hotspot ranking is built on.
    """
    indented = [w for w in widths if w > 0]
    if not indented:
        return TAB_WIDTH

    # The shallowest *well-supported* width is the first indent level, so no
    # step wider than it can be real. Widths carried by a stray wrapped line
    # do not count, or a single odd continuation would cap everything at 1.
    counts: Dict[int, int] = {}
    for width in indented:
        counts[width] = counts.get(width, 0) + 1
    floor = min(
        (w for w, n in counts.items() if n >= max(2, _WIDTH_SUPPORT * len(indented))),
        default=max(_UNIT_CANDIDATES),
    )

    for unit in _UNIT_CANDIDATES:
        if unit > floor:
            continue
        covered = sum(n for w, n in counts.items() if w % unit == 0)
        if covered >= _UNIT_COVERAGE * len(indented):
            return unit
    return TAB_WIDTH


def _classify(text: str, lang: Optional[Language]) -> Tuple[List[int], int, int, int]:
    """Split lines into indentation widths for code, plus comment/blank tallies."""
    line_comments = lang.line_comments if lang else ()
    block_comments = lang.block_comments if lang else ()

    widths: List[int] = []
    comments = 0
    blanks = 0
    longest = 0
    open_block: Optional[str] = None

    # A trailing newline terminates the last line; it does not begin a new
    # empty one. Splitting naively would count a phantom blank in every file.
    body = text[:-1] if text.endswith("\n") else text

    for raw in body.split("\n"):
        longest = max(longest, len(raw))
        width = _leading_width(raw)
        if width is None:
            blanks += 1
            continue

        stripped = raw.strip()

        if open_block is not None:
            comments += 1
            if open_block in stripped:
                open_block = None
            continue

        if any(stripped.startswith(token) for token in line_comments):
            comments += 1
            continue

        started_block = False
        for opener, closer in block_comments:
            if stripped.startswith(opener):
                comments += 1
                started_block = True
                # A block that opens and closes on one line stays closed.
                if closer not in stripped[len(opener):]:
                    open_block = closer
                break
        if started_block:
            continue

        if len(raw) > _MINIFIED_LINE:
            # One enormous line is a bundle; charging it for nesting is noise.
            continue

        widths.append(width)

    return widths, comments, blanks, longest


def analyse_text(text: str, lang: Optional[Language] = None) -> ComplexityStats:
    """Measure one file's worth of source."""
    if not text:
        return ComplexityStats()

    widths, comments, blanks, longest = _classify(text, lang)
    total_lines = text.count("\n") + (0 if text.endswith("\n") else 1)

    stats = ComplexityStats(
        lines=total_lines,
        code_lines=len(widths),
        comment_lines=comments,
        blank_lines=blanks,
        longest_line=longest,
    )
    if not widths:
        return stats

    unit = detect_indent_unit(widths)
    levels = [w // unit for w in widths]
    stats.total_indent = sum(levels)
    stats.max_indent = max(levels)

    mean = stats.total_indent / len(levels)
    variance = sum((level - mean) ** 2 for level in levels) / len(levels)
    stats.indent_stdev = variance ** 0.5
    return stats

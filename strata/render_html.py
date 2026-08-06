"""Building the standalone report.

The output is one HTML file with the data baked into it. No server, no CDN, no
build step — it opens from disk, it works on a plane, and it can be attached to
an email or committed next to the code it describes.
"""

from __future__ import annotations

import json
import os
from typing import Dict, List

from . import __version__
from .analyze import Report
from .pack import pack_tree

EXTENT = 1000.0
_TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "report.html")
_TOKEN = "__STRATA_PAYLOAD__"


def _friendly_root(root: str) -> str:
    home = os.path.expanduser("~")
    return "~" + root[len(home):] if home and root.startswith(home) else root


def build_payload(report: Report, top: int = 25) -> dict:
    nodes = pack_tree([(f.path, max(f.lines, 1)) for f in report.files], size=EXTENT)

    files: Dict[str, dict] = {}
    for record in report.files:
        entry = {
            "lang": record.lang,
            "lines": record.lines,
            "code": record.code_lines,
            "nest": record.max_indent,
            "fin": record.fan_in,
            "fout": record.fan_out,
            "heat": round(record.heat, 4),
        }
        if report.has_history:
            entry.update(
                commits=record.commits,
                churn=record.churn,
                authors=record.authors,
                owner=record.main_author,
                idle=round(record.idle_days, 1),
                age=round(record.age_days, 1),
            )
        files[record.path] = entry

    reverse: Dict[str, List[str]] = {}
    for source, targets in report.edges.items():
        for target in targets:
            reverse.setdefault(target, []).append(source)
    for targets in reverse.values():
        targets.sort()

    return {
        "meta": {
            "name": report.name,
            "root": _friendly_root(report.root),
            "generated_at": report.generated_at,
            "commits": report.commit_count,
            "span_days": round(report.span_days, 1),
            "has_history": report.has_history,
            "lines": report.total_lines,
            "half_life": int(report.half_life),
            "version": __version__,
            "extent": EXTENT,
        },
        "nodes": [[n.path, n.kind, n.depth, n.x, n.y, n.r] for n in nodes],
        "files": files,
        "edges": report.edges,
        "rdeps": reverse,
        "hotspots": [f.path for f in report.hotspots(limit=top)],
        "coupling": [
            {
                "left": p.left,
                "right": p.right,
                "degree": round(p.degree, 3),
                "co": p.co_changes,
                "hidden": p.hidden,
            }
            for p in report.coupling[:120]
        ],
        "cycles": report.cycle_paths[:12],
        "fossils": [f.path for f in report.fossils(limit=15)],
        "authors": [{"name": n, "commits": c, "files_owned": o} for n, c, o in report.authors[:12]],
        "languages": [{"name": n, "files": f, "lines": l} for n, f, l in report.languages[:12]],
        "findings": [
            {"severity": f.severity, "title": f.title, "detail": f.detail, "paths": f.paths}
            for f in report.findings
        ],
    }


def _escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def render(report: Report, top: int = 25) -> str:
    """Return the full HTML document for ``report``."""
    with open(_TEMPLATE, "r", encoding="utf-8") as handle:
        template = handle.read()

    payload = json.dumps(build_payload(report, top=top), separators=(",", ":"), ensure_ascii=False)
    # A path or author name containing "</script>" would otherwise end the
    # script element early. Escaping the angle brackets is valid JSON and valid
    # JavaScript, and costs nothing.
    payload = payload.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")

    return template.replace(_TOKEN, payload).replace("__TITLE__", _escape(report.name))


def write_html(report: Report, destination: str, top: int = 25) -> str:
    """Write the report to ``destination`` and return the absolute path."""
    html = render(report, top=top)
    destination = os.path.abspath(destination)
    parent = os.path.dirname(destination)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(destination, "w", encoding="utf-8") as handle:
        handle.write(html)
    return destination

"""Turning import statements into edges between files.

This is deliberately a resolver, not a parser. It reads import *specifiers* with
the patterns in :mod:`strata.langs`, then tries to point each one at a file that
actually exists in the tree. Specifiers that resolve become internal edges;
specifiers that do not are recorded as external dependencies, which turns out to
be an interesting report in its own right.

Being approximate is fine here. A missed edge slightly under-states fan-in; a
wrong edge would have to survive the "does this path exist" check, which almost
nothing spurious does.
"""

from __future__ import annotations

import os
import posixpath
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Set

from .discover import SourceFile
from .langs import language_for

_INDEX_STEMS = ("index", "mod", "main")

#: ``from <module> import <names>`` — both halves matter. ``from . import a, b``
#: names three different modules and mentions none of them in the module half.
_PY_FROM = re.compile(
    r"^[ \t]*from[ \t]+(\.*[\w\.]*)[ \t]+import[ \t]+(\([^)]*\)|[^\n#]+)", re.MULTILINE
)
_PY_IMPORT = re.compile(r"^[ \t]*import[ \t]+([\w\.]+(?:[ \t]*,[ \t]*[\w\.]+)*)", re.MULTILINE)

_PACKAGE_RE = re.compile(r"^\s*package\s+([\w\.]+)\s*;?", re.MULTILINE)
_NAMESPACE_RE = re.compile(r"^\s*namespace\s+([\w\.\\]+)", re.MULTILINE)
_GO_MODULE_RE = re.compile(r"^\s*module\s+(\S+)", re.MULTILINE)


@dataclass
class ImportGraph:
    #: file path -> set of file paths it imports
    edges: Dict[str, Set[str]] = field(default_factory=lambda: defaultdict(set))
    #: file path -> set of unresolved specifiers (third-party or stdlib)
    external: Dict[str, Set[str]] = field(default_factory=lambda: defaultdict(set))

    def fan_out(self, path: str) -> int:
        return len(self.edges.get(path, ()))

    def reverse(self) -> Dict[str, Set[str]]:
        back: Dict[str, Set[str]] = defaultdict(set)
        for src, dsts in self.edges.items():
            for dst in dsts:
                back[dst].add(src)
        return back


class _Index:
    """Every way we might want to look a file up, precomputed once."""

    def __init__(self, files: Sequence[SourceFile]) -> None:
        self.paths: Set[str] = {f.path for f in files}
        self.by_noext: Dict[str, List[str]] = defaultdict(list)
        self.by_basename: Dict[str, List[str]] = defaultdict(list)
        self.by_stem: Dict[str, List[str]] = defaultdict(list)
        self.by_dotted: Dict[str, str] = {}
        self.by_symbol: Dict[str, List[str]] = defaultdict(list)
        self.go_module: Optional[str] = None

        for f in files:
            path = f.path
            stem, ext = posixpath.splitext(path)
            self.by_noext[stem].append(path)
            self.by_basename[posixpath.basename(path)].append(path)
            self.by_stem[posixpath.basename(stem)].append(path)

            if ext in (".py", ".pyi"):
                dotted = stem[: -len("/__init__")] if stem.endswith("/__init__") else stem
                self.by_dotted.setdefault(dotted.replace("/", "."), path)

            if ext in (".java", ".kt", ".kts", ".scala"):
                match = _PACKAGE_RE.search(f.text)
                name = posixpath.basename(stem)
                if match:
                    self.by_symbol[f"{match.group(1)}.{name}"].append(path)
                self.by_symbol[name].append(path)

            if ext in (".cs", ".php"):
                for match in _NAMESPACE_RE.finditer(f.text):
                    ns = match.group(1).replace("\\", ".")
                    self.by_symbol[ns].append(path)
                    self.by_symbol[f"{ns}.{posixpath.basename(stem)}"].append(path)

            if posixpath.basename(path) == "go.mod":
                match = _GO_MODULE_RE.search(f.text)
                if match:
                    self.go_module = match.group(1)

    def exact(self, path: str) -> Optional[str]:
        return path if path in self.paths else None

    def with_exts(self, stem: str, exts: Sequence[str]) -> Optional[str]:
        """Try ``stem`` itself, then ``stem+ext``, then ``stem/index+ext``."""
        if not stem:
            return None
        if stem in self.paths:
            return stem
        for ext in exts:
            candidate = stem + ext
            if candidate in self.paths:
                return candidate
        for index in _INDEX_STEMS:
            for ext in exts:
                candidate = f"{stem}/{index}{ext}"
                if candidate in self.paths:
                    return candidate
        matches = self.by_noext.get(stem)
        return matches[0] if matches else None

    def unique_by_stem(self, stem: str) -> Optional[str]:
        matches = self.by_stem.get(stem)
        return matches[0] if matches and len(matches) == 1 else None

    def unique_symbol(self, symbol: str) -> Optional[str]:
        matches = self.by_symbol.get(symbol)
        return matches[0] if matches and len(matches) == 1 else None


def _normalise(path: str) -> str:
    cleaned = posixpath.normpath(path)
    return "" if cleaned == "." else cleaned.lstrip("./")


def _relative(from_file: str, spec: str) -> str:
    return _normalise(posixpath.join(posixpath.dirname(from_file), spec))


def _resolve_path_like(index: _Index, from_file: str, spec: str, exts: Sequence[str]) -> Optional[str]:
    if spec.startswith("."):
        return index.with_exts(_relative(from_file, spec), exts)
    # Bare specifiers may still be repo-root-relative (webpack/tsconfig aliases).
    for candidate in (spec, spec.lstrip("~@/")):
        hit = index.with_exts(_normalise(candidate), exts)
        if hit:
            return hit
    return None


def python_import_groups(text: str) -> List[Tuple[str, List[str]]]:
    """``(module, [candidate dotted specs])`` for every import in a Python file.

    Each group is resolved preferentially: any candidate that names a real
    submodule becomes an edge, and only if none do does the module itself get
    one. That distinguishes ``from pkg import submodule`` — an edge to the
    submodule — from ``from pkg import SomeClass``, an edge to the package.
    """
    groups: List[Tuple[str, List[str]]] = []

    for match in _PY_FROM.finditer(text):
        module, names_blob = match.group(1), match.group(2)
        if not module:
            continue
        names = []
        for raw in names_blob.strip().strip("()").split(","):
            name = raw.strip().split(" as ")[0].strip()
            if name and name != "*" and name.isidentifier():
                names.append(name)
        joiner = "" if module.endswith(".") else "."
        groups.append((module, [f"{module}{joiner}{name}" for name in names]))

    for match in _PY_IMPORT.finditer(text):
        for raw in match.group(1).split(","):
            name = raw.strip()
            if name:
                groups.append((name, [name]))

    return groups


def _resolve_python(index: _Index, from_file: str, spec: str) -> Optional[str]:
    if spec.startswith("."):
        ups = len(spec) - len(spec.lstrip("."))
        rest = spec[ups:]
        base = posixpath.dirname(from_file)
        for _ in range(ups - 1):
            base = posixpath.dirname(base)
        stem = posixpath.join(base, rest.replace(".", "/")) if rest else base
        return index.with_exts(_normalise(stem), (".py", ".pyi"))

    return index.by_dotted.get(spec) or index.with_exts(spec.replace(".", "/"), (".py", ".pyi"))


def _resolve_go(index: _Index, spec: str) -> Optional[str]:
    module = index.go_module
    if not module or not spec.startswith(module):
        return None
    package_dir = _normalise(spec[len(module):].lstrip("/"))
    matches = [
        p for p in index.paths
        if p.endswith(".go") and posixpath.dirname(p) == package_dir
    ]
    return sorted(matches)[0] if matches else None


def _resolve_rust(index: _Index, from_file: str, spec: str) -> Optional[str]:
    if "::" not in spec:  # `mod foo;`
        directory = posixpath.dirname(from_file)
        for candidate in (f"{directory}/{spec}.rs", f"{directory}/{spec}/mod.rs"):
            if _normalise(candidate) in index.paths:
                return _normalise(candidate)
        return index.unique_by_stem(spec)

    head, _, tail = spec.partition("::")
    parts = [p for p in tail.split("::") if p and "{" not in p and "*" not in p]
    if not parts:
        return None
    if head == "crate":
        roots = sorted(
            {posixpath.dirname(p) for p in index.paths if posixpath.basename(p) in ("lib.rs", "main.rs")}
        )
        bases = roots or [""]
    elif head == "super":
        bases = [posixpath.dirname(posixpath.dirname(from_file))]
    else:
        bases = [posixpath.dirname(from_file)]

    for base in bases:
        for depth in range(len(parts), 0, -1):
            stem = posixpath.join(base, "/".join(parts[:depth]))
            for candidate in (f"{stem}.rs", f"{stem}/mod.rs"):
                normalised = _normalise(candidate)
                if normalised in index.paths:
                    return normalised
    return None


def _resolve_include(index: _Index, from_file: str, spec: str) -> Optional[str]:
    hit = index.exact(_relative(from_file, spec))
    if hit:
        return hit
    hit = index.exact(_normalise(spec))
    if hit:
        return hit
    matches = index.by_basename.get(posixpath.basename(spec))
    return matches[0] if matches and len(matches) == 1 else None


def _resolve_dart(index: _Index, from_file: str, spec: str) -> Optional[str]:
    if spec.startswith("package:"):
        rest = spec[len("package:"):]
        _, _, tail = rest.partition("/")
        return index.with_exts(_normalise(f"lib/{tail}"), (".dart",)) or index.with_exts(
            _normalise(tail), (".dart",)
        )
    if spec.startswith("dart:"):
        return None
    return index.with_exts(_relative(from_file, spec), (".dart",))


def _resolve_godot(index: _Index, from_file: str, spec: str) -> Optional[str]:
    if spec.startswith("res://"):
        return index.exact(_normalise(spec[len("res://"):]))
    return index.exact(_relative(from_file, spec))


def _go_import_lines(text: str) -> Iterable[str]:
    """Only the lines inside ``import`` declarations.

    Go's import specifiers are bare quoted strings, so scanning the whole file
    for them would match every string literal in the program.
    """
    inside = False
    for line in text.split("\n"):
        stripped = line.strip()
        if inside:
            if stripped.startswith(")"):
                inside = False
            else:
                yield line
            continue
        if stripped.startswith("import ("):
            inside = True
        elif stripped.startswith("import "):
            yield line


def extract_specifiers(text: str, path: str) -> List[str]:
    """Raw import specifiers appearing in ``text``, in source order."""
    lang = language_for(path)
    if lang is None or not lang.import_patterns:
        return []

    if lang.name == "Go":
        text = "\n".join(_go_import_lines(text))

    seen: Set[str] = set()
    specs: List[str] = []
    for pattern in lang.import_patterns:
        for match in pattern.finditer(text):
            spec = match.group(1).strip()
            if spec and spec not in seen:
                seen.add(spec)
                specs.append(spec)
    return specs


def _external_name(spec: str) -> str:
    """Collapse a specifier to the package a human would name.

    ``@scope/pkg/deep/thing`` becomes ``@scope/pkg``; ``a.b.c`` becomes ``a``.
    """
    spec = spec.strip()
    if spec.startswith("@") and spec.count("/") >= 1:
        head, _, tail = spec.partition("/")
        return f"{head}/{tail.split('/')[0]}"
    if "/" in spec:
        return spec.split("/")[0]
    if "." in spec and not spec.startswith("."):
        return spec.split(".")[0]
    return spec


def _add_python_edges(graph: ImportGraph, index: _Index, path: str, text: str) -> None:
    """Resolve one Python file's imports, preferring submodules to packages."""
    for module, candidates in python_import_groups(text):
        hits = set()
        for candidate in candidates:
            target = _resolve_python(index, path, candidate)
            if target and target != path:
                hits.add(target)
        if hits:
            graph.edges[path].update(hits)
            continue
        target = _resolve_python(index, path, module)
        if target and target != path:
            graph.edges[path].add(target)
        elif not target:
            graph.external[path].add(_external_name(module))


def build_graph(files: Sequence[SourceFile]) -> ImportGraph:
    """Resolve every import in ``files`` into an :class:`ImportGraph`."""
    index = _Index(files)
    graph = ImportGraph()

    for source in files:
        lang = language_for(source.path)
        if lang is None or not lang.import_patterns:
            continue
        exts = lang.resolve_exts or (posixpath.splitext(source.path)[1],)

        if lang.resolver == "python":
            _add_python_edges(graph, index, source.path, source.text)
            continue

        for spec in extract_specifiers(source.text, source.path):
            target: Optional[str] = None
            if lang.resolver == "path":
                target = _resolve_path_like(index, source.path, spec, exts)
            elif lang.resolver == "go":
                target = _resolve_go(index, spec)
            elif lang.resolver == "rust":
                target = _resolve_rust(index, source.path, spec)
            elif lang.resolver == "jvm":
                target = index.unique_symbol(spec) or index.unique_symbol(spec.rpartition(".")[0])
            elif lang.resolver == "namespace":
                target = index.unique_symbol(spec.replace("\\", ".")) or _resolve_path_like(
                    index, source.path, spec, exts
                )
            elif lang.resolver == "include":
                target = _resolve_include(index, source.path, spec)
            elif lang.resolver == "dart":
                target = _resolve_dart(index, source.path, spec)
            elif lang.resolver == "godot":
                target = _resolve_godot(index, source.path, spec)

            if target and target != source.path:
                graph.edges[source.path].add(target)
            elif not target:
                graph.external[source.path].add(_external_name(spec))

    return graph


def directory_graph(graph: ImportGraph) -> Dict[str, Set[str]]:
    """Collapse file edges to directory edges, dropping self-loops."""
    collapsed: Dict[str, Set[str]] = defaultdict(set)
    for src, dsts in graph.edges.items():
        src_dir = posixpath.dirname(src) or "."
        for dst in dsts:
            dst_dir = posixpath.dirname(dst) or "."
            if src_dir != dst_dir:
                collapsed[src_dir].add(dst_dir)
    return collapsed


def relpath_key(path: str) -> str:
    """Normalise an OS path into the repo-relative form used as a key."""
    return path.replace(os.sep, "/")

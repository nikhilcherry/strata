"""Language table.

Everything ``strata`` knows about a programming language lives here: how to
recognise it from a filename, how to tell a comment from code, and how to spot
an import statement. Adding a language is adding one row.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Optional, Pattern, Sequence, Tuple


@dataclass(frozen=True)
class Language:
    name: str
    #: Tokens that begin a comment that runs to end of line.
    line_comments: Tuple[str, ...] = ()
    #: ``(open, close)`` pairs for comments that span lines.
    block_comments: Tuple[Tuple[str, str], ...] = ()
    #: Compiled patterns whose first group is a raw import specifier.
    import_patterns: Tuple[Pattern[str], ...] = ()
    #: Import specifiers are resolved by a named strategy in ``imports.py``.
    resolver: str = "none"
    #: Extra extensions tried when resolving a path-like specifier.
    resolve_exts: Tuple[str, ...] = ()


def _p(*patterns: str) -> Tuple[Pattern[str], ...]:
    return tuple(re.compile(p, re.MULTILINE) for p in patterns)


_JS_IMPORTS = _p(
    r"""^\s*import\s+[\s\S]{0,400}?from\s*['"]([^'"]+)['"]""",
    r"""^\s*import\s*['"]([^'"]+)['"]""",
    r"""^\s*export\s+[\s\S]{0,400}?from\s*['"]([^'"]+)['"]""",
    r"""require\(\s*['"]([^'"]+)['"]\s*\)""",
    r"""\bimport\(\s*['"]([^'"]+)['"]\s*\)""",
)

_JS_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts", ".vue", ".svelte")

_C_LIKE_BLOCK = (("/*", "*/"),)


LANGUAGES: Dict[str, Language] = {}


def _register(exts: Sequence[str], lang: Language) -> None:
    for ext in exts:
        LANGUAGES[ext] = lang


_register(
    [".py", ".pyi"],
    Language(
        name="Python",
        line_comments=("#",),
        block_comments=(('"""', '"""'), ("'''", "'''")),
        import_patterns=_p(
            r"^\s*import\s+([\w\.]+(?:\s*,\s*[\w\.]+)*)",
            r"^\s*from\s+(\.*[\w\.]*)\s+import\s",
        ),
        resolver="python",
    ),
)

_register(
    [".js", ".jsx", ".mjs", ".cjs"],
    Language("JavaScript", ("//",), _C_LIKE_BLOCK, _JS_IMPORTS, "path", _JS_EXTS),
)
_register(
    [".ts", ".tsx", ".mts", ".cts"],
    Language("TypeScript", ("//",), _C_LIKE_BLOCK, _JS_IMPORTS, "path", _JS_EXTS),
)
_register([".vue"], Language("Vue", ("//",), _C_LIKE_BLOCK, _JS_IMPORTS, "path", _JS_EXTS))
_register([".svelte"], Language("Svelte", ("//",), _C_LIKE_BLOCK, _JS_IMPORTS, "path", _JS_EXTS))

_register(
    [".go"],
    Language(
        name="Go",
        line_comments=("//",),
        block_comments=_C_LIKE_BLOCK,
        import_patterns=_p(r"""^\s*(?:[\w\.]+\s+)?"([^"]+)"\s*$"""),
        resolver="go",
    ),
)

_register(
    [".rs"],
    Language(
        name="Rust",
        line_comments=("//",),
        block_comments=_C_LIKE_BLOCK,
        import_patterns=_p(
            r"^\s*(?:pub(?:\([^)]*\))?\s+)?mod\s+(\w+)\s*;",
            r"^\s*(?:pub(?:\([^)]*\))?\s+)?use\s+((?:crate|self|super)(?:::[\w\*\{]+)+)",
        ),
        resolver="rust",
    ),
)

_register(
    [".java"],
    Language(
        name="Java",
        line_comments=("//",),
        block_comments=_C_LIKE_BLOCK,
        import_patterns=_p(r"^\s*import\s+(?:static\s+)?([\w\.\*]+)\s*;"),
        resolver="jvm",
    ),
)
_register(
    [".kt", ".kts"],
    Language(
        name="Kotlin",
        line_comments=("//",),
        block_comments=_C_LIKE_BLOCK,
        import_patterns=_p(r"^\s*import\s+([\w\.\*]+)"),
        resolver="jvm",
    ),
)

_register(
    [".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh", ".hxx", ".m", ".mm"],
    Language(
        name="C/C++",
        line_comments=("//",),
        block_comments=_C_LIKE_BLOCK,
        import_patterns=_p(r"""^\s*#\s*include\s+["<]([^">]+)[">]"""),
        resolver="include",
    ),
)

_register(
    [".dart"],
    Language(
        name="Dart",
        line_comments=("//",),
        block_comments=_C_LIKE_BLOCK,
        import_patterns=_p(r"""^\s*(?:import|export|part)\s+['"]([^'"]+)['"]"""),
        resolver="dart",
        resolve_exts=(".dart",),
    ),
)

_register(
    [".rb"],
    Language(
        name="Ruby",
        line_comments=("#",),
        block_comments=(("=begin", "=end"),),
        import_patterns=_p(
            r"""^\s*require_relative\s+['"]([^'"]+)['"]""",
            r"""^\s*require\s+['"]([^'"]+)['"]""",
        ),
        resolver="path",
        resolve_exts=(".rb",),
    ),
)

_register(
    [".php"],
    Language(
        name="PHP",
        line_comments=("//", "#"),
        block_comments=_C_LIKE_BLOCK,
        import_patterns=_p(
            r"^\s*use\s+([\w\\]+)\s*;",
            r"""^\s*(?:require|include)(?:_once)?\s*\(?\s*['"]([^'"]+)['"]""",
        ),
        resolver="namespace",
    ),
)

_register(
    [".cs"],
    Language(
        name="C#",
        line_comments=("//",),
        block_comments=_C_LIKE_BLOCK,
        import_patterns=_p(r"^\s*(?:global\s+)?using\s+(?:static\s+)?([\w\.]+)\s*;"),
        resolver="namespace",
    ),
)

_register([".swift"], Language("Swift", ("//",), _C_LIKE_BLOCK, (), "none"))
_register([".scala"], Language("Scala", ("//",), _C_LIKE_BLOCK, _p(r"^\s*import\s+([\w\._]+)"), "jvm"))
_register([".sh", ".bash", ".zsh"], Language("Shell", ("#",), ()))
_register([".sql"], Language("SQL", ("--",), _C_LIKE_BLOCK))
_register([".css", ".scss", ".sass", ".less"], Language("CSS", ("//",), _C_LIKE_BLOCK))
_register([".html", ".htm"], Language("HTML", (), (("<!--", "-->"),)))
_register([".md", ".markdown"], Language("Markdown", (), (("<!--", "-->"),)))
_register([".yml", ".yaml"], Language("YAML", ("#",), ()))
_register([".toml"], Language("TOML", ("#",), ()))
_register([".json"], Language("JSON", (), ()))
_register(
    [".lua"],
    Language("Lua", ("--",), (("--[[", "]]"),), _p(r"""require\s*\(?\s*['"]([^'"]+)['"]"""), "path", (".lua",)),
)
_register([".ex", ".exs"], Language("Elixir", ("#",), ()))
_register([".hs"], Language("Haskell", ("--",), (("{-", "-}"),)))
_register(
    [".gd"],
    Language("GDScript", ("#",), (), _p(r"""(?:preload|load)\(\s*['"]([^'"]+)['"]\s*\)"""), "godot"),
)
_register([".zig"], Language("Zig", ("//",), (), _p(r"""@import\(\s*"([^"]+)"\s*\)"""), "path", (".zig",)))


#: Extensions that hold prose or configuration rather than logic. They are
#: measured but never ranked as hotspots — a churning README is not a risk.
NON_CODE = frozenset(
    {".md", ".markdown", ".json", ".yml", ".yaml", ".toml", ".txt", ".csv", ".lock", ".cfg", ".ini"}
)

#: Directory names that hold somebody else's code, or a build artefact.
IGNORED_DIRS = frozenset(
    {
        ".git", ".hg", ".svn", ".idea", ".vscode", ".vs", "__pycache__", ".mypy_cache",
        ".pytest_cache", ".ruff_cache", ".tox", ".venv", "venv", "env", "node_modules",
        "bower_components", "vendor", "third_party", "dist", "build", "out", "target",
        "bin", "obj", ".next", ".nuxt", ".svelte-kit", ".gradle", ".dart_tool", "Pods",
        "coverage", "htmlcov", ".terraform", ".cache", "site-packages", ".serverless",
        ".angular", "DerivedData", ".expo", ".godot",
    }
)

#: Files that are generated, minified or vendored no matter where they live.
GENERATED_PATTERNS = tuple(
    re.compile(p)
    for p in (
        r"\.min\.(js|css)$",
        r"[-.]bundle\.js$",
        r"\.g\.dart$",
        r"\.freezed\.dart$",
        r"_pb2?\.py$",
        r"\.pb\.go$",
        r"_generated\.\w+$",
        r"(^|/)package-lock\.json$",
        r"(^|/)yarn\.lock$",
        r"(^|/)pnpm-lock\.yaml$",
        r"(^|/)poetry\.lock$",
        r"(^|/)Cargo\.lock$",
        r"(^|/)go\.sum$",
        r"(^|/)composer\.lock$",
    )
)

#: Path fragments that mark a file as test code.
TEST_HINTS = tuple(
    re.compile(p)
    for p in (
        r"(^|/)tests?(/|$)",
        r"(^|/)spec(/|$)",
        r"(^|/)__tests__(/|$)",
        r"(^|/)test_[^/]+$",
        r"[^/]+_test\.\w+$",
        r"[^/]+\.test\.\w+$",
        r"[^/]+\.spec\.\w+$",
        r"[^/]+Test\.(java|kt|cs|scala)$",
    )
)


def language_for(path: str) -> Optional[Language]:
    """Return the :class:`Language` for ``path``, or ``None`` if unknown."""
    dot = path.rfind(".")
    if dot == -1:
        return None
    return LANGUAGES.get(path[dot:].lower())


def is_generated(path: str) -> bool:
    return any(p.search(path) for p in GENERATED_PATTERNS)


def is_test(path: str) -> bool:
    return any(p.search(path) for p in TEST_HINTS)


def is_code(path: str) -> bool:
    """True when the file holds logic worth ranking, not prose or config."""
    if language_for(path) is None:
        return False
    dot = path.rfind(".")
    return path[dot:].lower() not in NON_CODE

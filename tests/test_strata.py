"""Tests for strata.

The history tests build real git repositories in a temporary directory and run
real git against them, because the thing most likely to break is our reading of
git's output, and a mock of git would only ever tell us what we already believe.
"""

from __future__ import annotations

import itertools
import json
import math
import os
import posixpath
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from strata.analyze import analyse, to_dict  # noqa: E402
from strata.complexity import analyse_text, detect_indent_unit  # noqa: E402
from strata.coupling import temporal_coupling  # noqa: E402
from strata.discover import discover  # noqa: E402
from strata.graph import cycle_path, strongly_connected  # noqa: E402
from strata.history import Commit, parse_log  # noqa: E402
from strata.imports import build_graph, extract_specifiers, python_import_groups  # noqa: E402
from strata.langs import is_generated, is_test, language_for  # noqa: E402
from strata.pack import Circle, enclose, pack_tree  # noqa: E402
from strata.render_html import render  # noqa: E402


def run(cwd, *args):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True)


class TempRepo:
    """A throwaway git repository with a deterministic, dated history."""

    def __init__(self):
        self.path = tempfile.mkdtemp(prefix="strata-test-")
        run(self.path, "git", "init", "-q", "-b", "main")
        run(self.path, "git", "config", "user.email", "test@example.com")
        run(self.path, "git", "config", "user.name", "Test Author")
        self.clock = 1_700_000_000

    def write(self, relative, text):
        full = os.path.join(self.path, relative)
        parent = os.path.dirname(full)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(full, "w", encoding="utf-8") as handle:
            handle.write(text)

    def commit(self, message, author="Test Author <test@example.com>", days=1):
        self.clock += days * 86400
        stamp = f"{self.clock} +0000"
        env = dict(
            os.environ,
            GIT_AUTHOR_DATE=stamp,
            GIT_COMMITTER_DATE=stamp,
            GIT_COMMITTER_NAME="Test Author",
            GIT_COMMITTER_EMAIL="test@example.com",
        )
        subprocess.run(["git", "add", "-A"], cwd=self.path, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", message, f"--author={author}"],
            cwd=self.path, check=True, capture_output=True, env=env,
        )

    def cleanup(self):
        shutil.rmtree(self.path, ignore_errors=True)


class ComplexityTests(unittest.TestCase):
    def test_flat_code_has_no_nesting_cost(self):
        stats = analyse_text("a = 1\nb = 2\nc = 3\n", language_for("x.py"))
        self.assertEqual(stats.code_lines, 3)
        self.assertEqual(stats.total_indent, 0)
        self.assertEqual(stats.max_indent, 0)

    def test_nesting_accumulates(self):
        source = "def f():\n    if x:\n        for i in y:\n            go()\n"
        stats = analyse_text(source, language_for("x.py"))
        self.assertEqual(stats.max_indent, 3)
        self.assertEqual(stats.total_indent, 0 + 1 + 2 + 3)

    def test_comments_and_blanks_are_not_code(self):
        source = "# note\n\ndef f():\n    pass\n"
        stats = analyse_text(source, language_for("x.py"))
        self.assertEqual(stats.code_lines, 2)
        self.assertEqual(stats.comment_lines, 1)
        self.assertEqual(stats.blank_lines, 1)

    def test_block_comments_are_skipped(self):
        source = "/* one\n   two */\nint x = 1;\n"
        stats = analyse_text(source, language_for("x.c"))
        self.assertEqual(stats.code_lines, 1)
        self.assertEqual(stats.comment_lines, 2)

    def test_single_line_block_comment_does_not_swallow_the_file(self):
        source = "/* one */\nint x = 1;\nint y = 2;\n"
        stats = analyse_text(source, language_for("x.c"))
        self.assertEqual(stats.code_lines, 2)

    def test_two_space_and_tab_indentation_measure_the_same(self):
        spaces = "def f():\n  if x:\n    go()\n"
        tabs = "def f():\n\tif x:\n\t\tgo()\n"
        self.assertEqual(
            analyse_text(spaces, language_for("x.py")).total_indent,
            analyse_text(tabs, language_for("x.py")).total_indent,
        )

    def test_indent_unit_detection(self):
        self.assertEqual(detect_indent_unit([0, 4, 8, 12]), 4)
        self.assertEqual(detect_indent_unit([0, 2, 4, 6]), 2)
        self.assertEqual(detect_indent_unit([]), 4)

    def test_empty_text_is_safe(self):
        self.assertEqual(analyse_text("").lines, 0)


class LanguageTests(unittest.TestCase):
    def test_detects_by_extension(self):
        self.assertEqual(language_for("a/b.py").name, "Python")
        self.assertEqual(language_for("a/b.TS").name, "TypeScript")
        self.assertIsNone(language_for("Makefile"))

    def test_generated_and_test_detection(self):
        self.assertTrue(is_generated("web/app.min.js"))
        self.assertTrue(is_generated("api/models_pb2.py"))
        self.assertFalse(is_generated("src/app.js"))
        self.assertTrue(is_test("tests/test_thing.py"))
        self.assertTrue(is_test("src/thing.spec.ts"))
        self.assertFalse(is_test("src/thing.ts"))


class ImportTests(unittest.TestCase):
    def test_python_groups_expand_names(self):
        groups = python_import_groups("from . import alpha, beta\n")
        self.assertEqual(groups, [(".", [".alpha", ".beta"])])

    def test_python_from_package_prefers_submodule(self):
        groups = python_import_groups("from pkg import mod\n")
        self.assertEqual(groups, [("pkg", ["pkg.mod"])])

    def test_go_import_specifiers_ignore_ordinary_strings(self):
        source = (
            'package main\n\nimport (\n\t"fmt"\n)\n\n'
            'func main() {\n\tprintln("not/an/import")\n}\n'
        )
        self.assertEqual(extract_specifiers(source, "main.go"), ["fmt"])

    def test_resolution_across_languages(self):
        repo = TempRepo()
        try:
            repo.write("pkg/__init__.py", "")
            repo.write("pkg/core.py", "VALUE = 1\n")
            repo.write("pkg/app.py", "from . import core\n")
            repo.write("web/util.ts", "export const a = 1;\n")
            repo.write("web/main.ts", "import { a } from './util';\n")
            repo.commit("init")

            files, _ = discover(repo.path)
            graph = build_graph(files)
            self.assertIn("pkg/core.py", graph.edges["pkg/app.py"])
            self.assertIn("web/util.ts", graph.edges["web/main.ts"])
        finally:
            repo.cleanup()

    def test_unresolved_specifiers_become_external(self):
        repo = TempRepo()
        try:
            repo.write("a.py", "import requests\nfrom numpy import array\n")
            repo.commit("init")
            files, _ = discover(repo.path)
            graph = build_graph(files)
            self.assertEqual(graph.external["a.py"], {"requests", "numpy"})
        finally:
            repo.cleanup()


class GraphTests(unittest.TestCase):
    def test_finds_a_cycle_and_ignores_a_chain(self):
        edges = {"a": {"b"}, "b": {"c"}, "c": {"a"}, "d": {"a"}}
        components = strongly_connected(edges)
        self.assertEqual(len(components), 1)
        self.assertEqual(components[0], ["a", "b", "c"])

    def test_cycle_path_returns_a_round_trip(self):
        edges = {"a": {"b"}, "b": {"c"}, "c": {"a"}}
        path = cycle_path(edges, ["a", "b", "c"])
        self.assertEqual(path[0], path[-1])
        self.assertEqual(path, ["a", "b", "c", "a"])

    def test_deep_chain_does_not_overflow_the_stack(self):
        edges = {str(i): {str(i + 1)} for i in range(6000)}
        edges["6000"] = {"0"}
        components = strongly_connected(edges)
        self.assertEqual(len(components[0]), 6001)


class HistoryTests(unittest.TestCase):
    def test_parses_numstat_and_counts_churn(self):
        raw = (
            "\x1eabc\x1f1700000000\x1fAda\x1ffirst\n"
            "10\t2\tsrc/a.py\n"
            "1\t0\tsrc/b.py\n"
            "\x1edef\x1f1700086400\x1fGrace\x1fsecond\n"
            "3\t3\tsrc/a.py\n"
        )
        history = parse_log(raw)
        self.assertEqual(len(history.commits), 2)
        self.assertEqual(history.files["src/a.py"].commits, 2)
        self.assertEqual(history.files["src/a.py"].added, 13)
        self.assertEqual(history.files["src/a.py"].deleted, 5)
        self.assertEqual(set(history.files["src/a.py"].authors), {"Ada", "Grace"})

    def test_renames_carry_history_forward(self):
        # Newest first, as git reports it: the rename commit maps old -> new.
        raw = (
            "\x1eb\x1f1700086400\x1fAda\x1frename\n"
            "1\t1\tsrc/{old.py => new.py}\n"
            "\x1ea\x1f1700000000\x1fAda\x1fcreate\n"
            "50\t0\tsrc/old.py\n"
        )
        history = parse_log(raw)
        self.assertNotIn("src/old.py", history.files)
        self.assertEqual(history.files["src/new.py"].commits, 2)
        self.assertEqual(history.files["src/new.py"].added, 51)

    def test_simple_rename_form(self):
        raw = "\x1ea\x1f1700000000\x1fAda\x1fmv\n1\t1\told.py => new.py\n"
        history = parse_log(raw)
        self.assertIn("new.py", history.files)

    def test_binary_files_do_not_break_parsing(self):
        raw = "\x1ea\x1f1700000000\x1fAda\x1fadd\n-\t-\timage.png\n"
        history = parse_log(raw)
        self.assertEqual(history.files["image.png"].added, 0)

    def test_known_paths_filter_out_deleted_files(self):
        raw = "\x1ea\x1f1700000000\x1fAda\x1fx\n1\t0\tgone.py\n1\t0\there.py\n"
        history = parse_log(raw, known={"here.py"})
        self.assertEqual(set(history.files), {"here.py"})


class CouplingTests(unittest.TestCase):
    def test_pairs_that_always_change_together_score_highest(self):
        commits = [Commit("s", 0, "a", "m", ["a.py", "b.py"]) for _ in range(5)]
        commits.append(Commit("s", 0, "a", "m", ["a.py", "c.py"]))
        changes = {"a.py": 6, "b.py": 5, "c.py": 1}
        pairs = temporal_coupling(commits, changes, min_co_changes=4)
        self.assertEqual(len(pairs), 1)
        self.assertEqual((pairs[0].left, pairs[0].right), ("a.py", "b.py"))
        self.assertAlmostEqual(pairs[0].degree, 1.0)

    def test_bulk_commits_are_ignored(self):
        wide = [f"f{i}.py" for i in range(60)]
        commits = [Commit("s", 0, "a", "sweep", wide) for _ in range(10)]
        changes = {name: 10 for name in wide}
        self.assertEqual(temporal_coupling(commits, changes, bulk_threshold=40), [])


class PackTests(unittest.TestCase):
    def test_enclosing_circle_contains_every_input(self):
        circles = [Circle(0, 0, 5), Circle(20, 0, 3), Circle(0, 14, 2)]
        hull = enclose(circles)
        for circle in circles:
            distance = math.hypot(circle.x - hull.x, circle.y - hull.y)
            self.assertLessEqual(distance + circle.r, hull.r + 1e-6)

    def test_siblings_never_overlap_and_stay_inside_their_parent(self):
        entries = [
            (f"pkg{p}/sub{s}/file{i}.py", 10 + (i * 37) % 500)
            for p in range(4) for s in range(3) for i in range(9)
        ]
        nodes = pack_tree(entries, size=1000.0)
        by_path = {n.path: n for n in nodes}

        siblings = {}
        for node in nodes:
            siblings.setdefault(posixpath.dirname(node.path), []).append(node)
        for group in siblings.values():
            for a, b in itertools.combinations(group, 2):
                gap = math.hypot(a.x - b.x, a.y - b.y) - (a.r + b.r)
                self.assertGreater(gap, -0.05, f"{a.path} overlaps {b.path}")

        for node in nodes:
            parent = by_path.get(posixpath.dirname(node.path))
            if parent:
                escape = math.hypot(node.x - parent.x, node.y - parent.y) + node.r - parent.r
                self.assertLess(escape, 0.05, f"{node.path} escapes {parent.path}")

    def test_layout_is_deterministic(self):
        entries = [(f"a/f{i}.py", i * 13 % 400 + 5) for i in range(40)]
        first = [(n.path, n.x, n.y, n.r) for n in pack_tree(entries)]
        second = [(n.path, n.x, n.y, n.r) for n in pack_tree(entries)]
        self.assertEqual(first, second)

    def test_area_tracks_value(self):
        nodes = pack_tree([("a/small.py", 100), ("a/big.py", 400)])
        by_path = {n.path: n for n in nodes}
        ratio = by_path["a/big.py"].r / by_path["a/small.py"].r
        self.assertAlmostEqual(ratio, 2.0, places=1)

    def test_empty_input(self):
        self.assertEqual(pack_tree([]), [])


class AnalyseTests(unittest.TestCase):
    def setUp(self):
        self.repo = TempRepo()
        self.repo.write("app/__init__.py", "")
        self.repo.write("app/util.py", "def helper():\n    return 1\n")
        self.repo.write(
            "app/core.py",
            "from . import util\n\n\ndef run(items):\n"
            + "".join(f"    if items[{i}]:\n        pass\n" for i in range(12)),
        )
        self.repo.write("tests/test_core.py", "from app import core\n\n\ndef test_run():\n    pass\n")
        self.repo.commit("init")

        for i in range(4):
            self.repo.write(
                "app/core.py",
                f"from . import util\n\n\ndef run(items):  # rev {i}\n"
                + "".join(f"    if items[{j}]:\n        pass\n" for j in range(12 + i)),
            )
            self.repo.write(
                "tests/test_core.py",
                f"from app import core\n\n\ndef test_run():\n    pass  # {i}\n",
            )
            self.repo.commit(f"change {i}", author="Second Dev <second@example.com>")

    def tearDown(self):
        self.repo.cleanup()

    def test_the_busiest_and_most_nested_file_is_the_hotspot(self):
        report = analyse(self.repo.path)
        self.assertTrue(report.has_history)
        self.assertEqual(report.hotspots(limit=1)[0].path, "app/core.py")

    def test_imports_resolve_into_fan_in(self):
        report = analyse(self.repo.path)
        util = next(f for f in report.files if f.path == "app/util.py")
        self.assertEqual(util.fan_in, 1)

    def test_coupling_between_a_module_and_its_test_is_not_hidden(self):
        report = analyse(self.repo.path, min_co_changes=3)
        pairs = {(p.left, p.right): p for p in report.coupling}
        pair = pairs.get(("app/core.py", "tests/test_core.py"))
        self.assertIsNotNone(pair, "expected core and its test to be coupled")
        self.assertFalse(pair.hidden, "the test imports the module, so the link is visible")

    def test_authorship_is_tracked(self):
        report = analyse(self.repo.path)
        names = {name for name, _, _ in report.authors}
        self.assertEqual(names, {"Test Author", "Second Dev"})

    def test_no_history_mode_still_ranks(self):
        report = analyse(self.repo.path, no_history=True)
        self.assertFalse(report.has_history)
        self.assertEqual(report.hotspots(limit=1)[0].path, "app/core.py")
        self.assertEqual(report.commit_count, 0)

    def test_json_export_round_trips(self):
        payload = json.loads(json.dumps(to_dict(analyse(self.repo.path))))
        self.assertEqual(payload["name"], os.path.basename(self.repo.path))
        self.assertTrue(any(f["path"] == "app/core.py" for f in payload["files"]))

    def test_html_report_is_self_contained(self):
        html = render(analyse(self.repo.path))
        self.assertIn("<canvas", html)
        self.assertNotIn("__STRATA_PAYLOAD__", html)
        self.assertNotIn("<script src=", html)
        self.assertNotIn("<link rel=\"stylesheet\"", html)

    def test_gitignored_files_are_not_analysed(self):
        self.repo.write(".gitignore", "secret.py\n")
        self.repo.write("secret.py", "TOKEN = 'x'\n")
        self.repo.commit("ignore")
        report = analyse(self.repo.path)
        self.assertNotIn("secret.py", {f.path for f in report.files})


class NonRepoTests(unittest.TestCase):
    def test_plain_directory_works_without_git(self):
        directory = tempfile.mkdtemp(prefix="strata-plain-")
        try:
            with open(os.path.join(directory, "a.py"), "w", encoding="utf-8") as handle:
                handle.write("def f():\n    if x:\n        pass\n")
            report = analyse(directory)
            self.assertFalse(report.has_history)
            self.assertEqual(len(report.files), 1)
            self.assertGreater(report.hotspots(limit=1)[0].heat, 0)
        finally:
            shutil.rmtree(directory, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)

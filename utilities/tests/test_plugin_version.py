# SPDX-FileCopyrightText: 2026 Curtis Galloway
# SPDX-License-Identifier: Apache-2.0
"""Tests for utilities/plugin-version.py."""

from __future__ import annotations

import contextlib
import datetime
import importlib.util
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("plugin_version",
                                              HERE.parent / "plugin-version.py")
pv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pv)

GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
}
SEP27 = datetime.date(2026, 9, 27)


class NextVersionTest(unittest.TestCase):

    def test_first_release_of_the_day(self) -> None:
        self.assertEqual(pv.next_version("1.0.0", SEP27), "2026.927.0")

    def test_second_release_of_the_day_counts_up(self) -> None:
        self.assertEqual(pv.next_version("2026.927.0", SEP27), "2026.927.1")

    def test_month_rollover_sorts_higher(self) -> None:
        new = pv.next_version("2026.930.3", datetime.date(2026, 10, 1))
        self.assertEqual(new, "2026.1001.0")
        self.assertGreater(pv.parse(new), pv.parse("2026.930.3"))

    def test_a_clock_behind_the_last_version_still_goes_up(self) -> None:
        self.assertEqual(pv.next_version("2026.1001.0", SEP27), "2026.1001.1")

    def test_parse_rejects_other_shapes(self) -> None:
        for bad in ("1.0.0", "2026.9.27", "2026.0927.0", "2026.1301.0", "2026.927.01"):
            self.assertIsNone(pv.parse(bad), bad)


class RepoTest(unittest.TestCase):
    """A marketplace with a themed plugin and a bundle that covers it."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.write(".claude-plugin/marketplace.json", json.dumps({"plugins": [
            {"name": "tools", "source": "./plugins/tools"},
            {"name": "all", "source": "./", "skills": ["./plugins/tools/skills"]},
        ]}))
        self.write(".claude-plugin/plugin.json", '{\n  "name": "all",\n  "version": "3.0.0"\n}\n')
        self.write("plugins/tools/.claude-plugin/plugin.json",
                   '{"name": "tools", "version": "1.0.0"}\n')
        self.write("plugins/tools/skills/hammer/SKILL.md", "one\n")
        self.write("README.md", "readme\n")
        self.git("init", "-q", "-b", "main")
        self.git("add", ".")
        self.git("commit", "-qm", "base")
        self.git("switch", "-qc", "work")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write(self, rel: str, text: str) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def git(self, *args: str) -> None:
        subprocess.run(["git", *args], cwd=self.root, env=GIT_ENV, check=True)

    def run_tool(self, *args: str) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            rc = pv.main(["--root", str(self.root), *args])
        return rc, out.getvalue()

    def version(self, rel: str) -> str:
        return json.loads((self.root / rel).read_text())["version"]

    def test_unrelated_change_needs_no_bump(self) -> None:
        self.write("README.md", "changed\n")
        self.assertEqual(self.run_tool("check", "--base", "main")[0], 0)

    def test_skill_change_fails_until_both_plugins_are_bumped(self) -> None:
        self.write("plugins/tools/skills/hammer/SKILL.md", "two\n")
        rc, out = self.run_tool("check", "--base", "main")
        self.assertEqual(rc, 1)
        self.assertIn("tools: files changed", out)
        self.assertIn("all: files changed", out)

        self.run_tool("bump", "--base", "main", "--date", "2026-09-27")
        self.assertEqual(self.version("plugins/tools/.claude-plugin/plugin.json"),
                         "2026.927.0")
        self.assertEqual(self.version(".claude-plugin/plugin.json"), "2026.927.0")
        self.assertEqual(self.run_tool("check", "--base", "main")[0], 0)

    def test_bump_is_idempotent_within_a_branch(self) -> None:
        self.write("plugins/tools/skills/hammer/SKILL.md", "two\n")
        self.run_tool("bump", "--base", "main", "--date", "2026-09-27")
        rc, out = self.run_tool("bump", "--base", "main", "--date", "2026-09-27")
        self.assertEqual(rc, 0)
        self.assertIn("nothing to bump", out)
        self.assertEqual(self.version(".claude-plugin/plugin.json"), "2026.927.0")

    def test_bump_keeps_the_rest_of_the_file(self) -> None:
        self.run_tool("bump", "--date", "2026-09-27", "all")
        self.assertEqual((self.root / ".claude-plugin/plugin.json").read_text(),
                         '{\n  "name": "all",\n  "version": "2026.927.0"\n}\n')

    def test_malformed_version_fails(self) -> None:
        self.write("plugins/tools/skills/hammer/SKILL.md", "two\n")
        self.write("plugins/tools/.claude-plugin/plugin.json",
                   '{"name": "tools", "version": "2026.9.27a"}\n')
        rc, out = self.run_tool("check", "--base", "main")
        self.assertEqual(rc, 1)
        self.assertIn("is not YYYY.MDD.N", out)

    def test_a_version_in_the_marketplace_entry_fails(self) -> None:
        mkt = json.loads((self.root / ".claude-plugin/marketplace.json").read_text())
        mkt["plugins"][0]["version"] = "1.0.0"
        self.write(".claude-plugin/marketplace.json", json.dumps(mkt))
        rc, out = self.run_tool("check", "--base", "main")
        self.assertEqual(rc, 1)
        self.assertIn("keep it only in plugin.json", out)


if __name__ == "__main__":
    unittest.main()

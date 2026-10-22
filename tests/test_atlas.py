"""Deterministic tests for Project Atlas.  No network, no model, no Hermes process.

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from atlas_core import build, facts, model, render, structure


def write(path: Path, text: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class ScanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_manifest_entrypoint_and_service(self) -> None:
        write(self.root / "package.json", json.dumps({
            "name": "demo", "workspaces": ["apps/*"],
            "scripts": {"dev": "vite"}, "dependencies": {"react": "19"},
        }))
        write(self.root / "apps" / "web" / "src" / "main.tsx", "console.log('x')\n")
        write(self.root / "apps" / "web" / "package.json", json.dumps({"name": "web", "main": "src/main.tsx"}))
        write(self.root / "docker-compose.yml", "services:\n  web:\n    build: ./apps/web\n")
        write(self.root / "README.md", "# Demo\n")

        data = facts.scan(self.root)
        self.assertEqual(data["name"], self.root.name)
        self.assertGreaterEqual(data["inventory"]["total"], 4)
        kinds = {m["kind"] for m in data["manifests"]}
        self.assertIn("package.json", kinds)
        self.assertIn("compose", kinds)
        self.assertTrue(data["services"])
        self.assertEqual(data["services"][0]["name"], "web")
        self.assertTrue(any(e["path"].endswith("main.tsx") for e in data["entrypoints"]))
        self.assertTrue((self.root / ".atlas" / "facts.json").exists())

    def test_secrets_and_ignored_dirs_are_not_scanned(self) -> None:
        write(self.root / ".env", "TOKEN=super-secret\n")
        write(self.root / "keys" / "id_rsa", "PRIVATE\n")
        write(self.root / "keys" / "real.pem", "PRIVATE\n")
        write(self.root / "src" / "app.py", "print('ok')\n")
        write(self.root / "node_modules" / "pkg" / "index.js", "no\n")

        data = facts.scan(self.root)
        names = {entry["path"] for entry in data["inventory"]["top"]}
        self.assertNotIn(".env", names)
        self.assertTrue((self.root / "src" / "app.py").exists())
        self.assertFalse(any("node_modules" in str(d.get("path")) for d in data["inventory"]["dirs"]))
        self.assertFalse(any("id_rsa" in str(d.get("path")) for d in data["inventory"]["dirs"]))


class StructureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_workspace_and_cli_get_different_shapes(self) -> None:
        write(self.root / "package.json", json.dumps({"name": "mono", "workspaces": ["apps/*", "packages/*"]}))
        for name in ("web", "admin"):
            write(self.root / "apps" / name / "src" / "main.tsx", "x\n")
            write(self.root / "apps" / name / "package.json", json.dumps({"name": name}))
        for name in ("ui", "api"):
            write(self.root / "packages" / name / "src" / "index.ts", "x\n")
            write(self.root / "packages" / name / "package.json", json.dumps({"name": name}))
        mono_facts = facts.scan(self.root)
        mono = structure.propose(mono_facts, self.root)

        cli = Path(self.tmp.name) / "cli"
        write(cli / "pyproject.toml", "[project]\nname='tool'\n[project.scripts]\ntool='tool.cli:main'\n")
        write(cli / "tool" / "__init__.py", "")
        write(cli / "tool" / "cli.py", "def main(): pass\n")
        write(cli / "bin" / "tool", "#!/usr/bin/env python3\n")
        cli_facts = facts.scan(cli)
        cli_tree = structure.propose(cli_facts, cli)

        mono_names = {n["name"] for n in mono["nodes"]}
        cli_names = {n["name"] for n in cli_tree["nodes"]}
        self.assertNotEqual(mono_names, cli_names)
        self.assertTrue(mono_names)
        self.assertTrue(cli_names)
        for node in mono["nodes"]:
            for rel in node["paths"]:
                self.assertTrue((self.root / rel).exists(), rel)
        for node in cli_tree["nodes"]:
            for rel in node["paths"]:
                self.assertTrue((cli / rel).exists(), rel)

    def test_every_node_has_a_real_path(self) -> None:
        write(self.root / "README.md", "# X\n")
        write(self.root / "src" / "chat" / "index.ts", "x\n")
        write(self.root / "src" / "settings" / "index.ts", "x\n")
        write(self.root / "src" / "chat" / "components" / "Message.tsx", "x\n")
        tree = structure.propose(facts.scan(self.root), self.root)
        self.assertTrue(tree["nodes"])
        for node in tree["nodes"]:
            self.assertTrue(node["paths"])
            for rel in node["paths"]:
                self.assertTrue((self.root / rel).exists(), rel)
            self.assertRegex(node["sourceHash"], r"^[0-9a-f]{12}$")


class TreeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        write(self.root / "README.md", "# Demo\n")
        write(self.root / "app.py", "print('hi')\n")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _tree(self) -> dict:
        return {
            "version": 1, "project": "demo", "generated": model.utcnow(), "generator": "test",
            "nodes": [
                {"id": "core", "name": "Core", "parent": "root", "kind": "library",
                 "summary": "s", "status": "full", "confidence": 0.9, "locked": False,
                 "paths": ["app.py"], "sourceHash": "a" * 12, "decisions": [], "links": [], "todo": []},
            ],
        }

    def test_validation_rejects_bad_parent_and_link(self) -> None:
        tree = self._tree()
        tree["nodes"][0]["parent"] = "missing"
        errors, _ = model.validate(tree, self.root)
        self.assertTrue(any("parent" in e for e in errors))

        tree = self._tree()
        tree["nodes"][0]["links"] = [{"to": "nope", "type": "uses"}]
        errors, _ = model.validate(tree, self.root)
        self.assertTrue(any("link target" in e for e in errors))

    def test_roundtrip_keeps_comments_and_merge_keeps_locked(self) -> None:
        path = model.tree_path(self.root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "# keep this comment\nversion: 1\nproject: demo\ngenerated: '2026-01-01T00:00:00+00:00'\n"
            "generator: test\nnodes:\n  - id: core\n    name: Core\n    parent: root\n    kind: library\n"
            "    summary: old\n    status: full\n    confidence: 0.9\n    locked: true\n"
            "    paths: [app.py]\n    sourceHash: aaaaaaaaaaaa\n    decisions: []\n    links: []\n    todo: []\n",
            encoding="utf-8",
        )
        tree = model.load_tree_raw(self.root)
        tree["nodes"][0]["summary"] = "new"
        model.save_tree_raw(self.root, tree)
        text = path.read_text(encoding="utf-8")
        self.assertIn("# keep this comment", text)

        draft = structure.propose(facts.scan(self.root), self.root)
        merged, changes = structure.merge(model.load_tree_raw(self.root), draft, self.root)
        core = model.by_id(merged["nodes"])["core"]
        self.assertTrue(core["locked"])
        self.assertEqual(core["name"], "Core")
        self.assertIn("core", changes["kept"])

    def test_hash_changes_with_content(self) -> None:
        before = facts.hash_paths(self.root, ["app.py"])["hash"]
        write(self.root / "app.py", "print('changed')\n")
        after = facts.hash_paths(self.root, ["app.py"])["hash"]
        self.assertNotEqual(before, after)

    def test_render_works_from_yaml_only(self) -> None:
        model.save_tree(self.root, self._tree())
        facts_path = model.facts_path(self.root)
        if facts_path.exists():
            facts_path.unlink()
        state = render.state_payload(self.root, with_stale=False)
        self.assertTrue(state["exists"])
        self.assertEqual(state["coverage"]["total"], 1)
        self.assertEqual(state["nodes"][0]["summary"], "s")


class BuildTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        write(self.root / "README.md", "# Demo\nA tiny service.\n")
        write(self.root / "app.py", "def main():\n    return 1\n")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _fake(self, summary: str = "Fake summary", status: str = "full"):
        class Fake:
            def complete_structured(self, **kwargs):
                return SimpleNamespace(parsed={
                    "summary": summary,
                    "overview": "This is a sufficiently long overview of the node, grounded in the code excerpt. " * 3,
                    "status": status,
                    "confidence": 0.8,
                    "kind": "library",
                    "decisions": [{
                        "title": "Single entrypoint",
                        "why": "The module exposes main().",
                        "evidence": ["app.py"],
                    }],
                    "links": [],
                    "todo": [],
                }, text="")
        return Fake()

    def test_build_writes_valid_yaml_and_filters_bad_evidence(self) -> None:
        facts.scan(self.root)
        tree = structure.propose(model.read_json(model.facts_path(self.root)), self.root)
        planned = build.plan(self.root, tree, model.read_json(model.facts_path(self.root)), "all", build.BuildConfig())
        result = build.build(self.root, llm=self._fake(), scope="all", confirm=planned["hash"])
        self.assertTrue(result["ok"])
        saved = model.load_tree(self.root)
        self.assertEqual(saved["nodes"][0]["status"], "full")
        self.assertEqual(len(saved["nodes"][0]["decisions"]), 1)
        errors, _ = model.validate(saved, self.root)
        self.assertFalse(errors)

    def test_locked_node_is_never_rewritten(self) -> None:
        facts.scan(self.root)
        tree = structure.propose(model.read_json(model.facts_path(self.root)), self.root)
        tree["nodes"][0]["locked"] = True
        tree["nodes"][0]["summary"] = "human words"
        model.save_tree(self.root, tree)
        build.build(self.root, llm=self._fake("agent words"), scope="node:" + tree["nodes"][0]["id"])
        saved = model.load_tree(self.root)
        self.assertEqual(saved["nodes"][0]["summary"], "human words")
        self.assertTrue(saved["nodes"][0]["locked"])


if __name__ == "__main__":
    unittest.main()

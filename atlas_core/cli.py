"""Standalone CLI for the deterministic half.

    python -m atlas_core.cli scan  --project ~/algo/local-sync
    python -m atlas_core.cli draft --project ~/algo/local-sync
    python -m atlas_core.cli tree  --project ~/algo/local-sync
    python -m atlas_core.cli build --project ~/algo/local-sync   # needs a model

`scan`/`draft`/`tree`/`validate` need no Hermes process and no credentials, which
is how the scanner is tested against different project shapes.  `build` without
a model only refreshes hashes and the structural draft; it never invents prose.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import build as build_mod
from . import facts as facts_mod
from . import model, render, structure


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def main(argv: list[str] | None = None, *, llm: Any = None, cfg: build_mod.BuildConfig | None = None) -> int:
    parser = argparse.ArgumentParser(prog="atlas", description="Project Atlas — project map and knowledge base")
    parser.add_argument("command", choices=["scan", "draft", "plan", "build", "validate", "tree", "doctor"])
    parser.add_argument("--project", "-p", default=".", help="project directory (default: cwd)")
    parser.add_argument("--scope", default="stale", help="build scope: stale | all | undescribed | node:<id>")
    parser.add_argument("--deep", action="store_true", help="ask the model to refine the branch list too")
    parser.add_argument("--confirm", default=None, help="plan hash printed by `plan` (required for a full build)")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)
    root = Path(args.project).expanduser().resolve()
    cfg = cfg or build_mod.BuildConfig()

    if args.command == "doctor":
        payload = {
            "root": str(root),
            "ruamel": _probe("ruamel.yaml"),
            "jsonschema": _probe("jsonschema"),
            "writable": _writable(root),
            "atlas": model.tree_path(root).exists(),
        }
        print(_json(payload))
        return 0

    if args.command == "scan":
        facts = facts_mod.scan(root)
        summary = render.facts_summary(facts)
        print(_json(summary) if args.json else _scan_text(summary, facts))
        return 0

    if args.command == "draft":
        facts = model.read_json(model.facts_path(root)) or facts_mod.scan(root)
        tree = structure.propose(facts, root, max_branches=cfg.max_branches, max_depth=cfg.max_depth)
        path = model.save_tree(root, tree)
        print(_json({"ok": True, "path": str(path), "nodes": len(tree["nodes"]), "orphans": len(tree["orphans"])}) if args.json
              else render.tree_text(tree) + f"\n\nзаписано: {path}")
        return 0

    if args.command == "tree":
        tree = model.load_tree(root)
        if not tree:
            print(f"нет {model.tree_path(root)} — сначала `atlas draft` или `atlas build`", file=sys.stderr)
            return 2
        print(_json(tree) if args.json else render.tree_text(tree))
        return 0

    if args.command == "validate":
        tree = model.load_tree(root)
        if not tree:
            print(f"нет {model.tree_path(root)}", file=sys.stderr)
            return 2
        errors, warnings = model.validate(tree, root)
        print(_json({"ok": not errors, "errors": errors, "warnings": warnings}))
        return 0 if not errors else 1

    if args.command == "plan":
        facts = model.read_json(model.facts_path(root)) or facts_mod.scan(root)
        tree = model.load_tree(root) or structure.propose(facts, root, max_branches=cfg.max_branches, max_depth=cfg.max_depth)
        payload = build_mod.plan(root, tree, facts, args.scope, cfg)
        print(_json(payload))
        return 0

    if args.command == "build":
        try:
            result = build_mod.build(
                root, llm=llm, scope=args.scope, cfg=cfg, deep=args.deep, confirm=args.confirm,
                )
        except ValueError as exc:
            print(_json({"ok": False, "error": str(exc)}), file=sys.stderr)
            return 1
        tree = model.load_tree(root)
        print(_json(result) if args.json else render.tree_text(tree) + "\n\n" + _json({k: v for k, v in result.items() if k != "coverage"}))
        return 0

    return 2


def _probe(module: str) -> bool:
    try:
        __import__(module)
        return True
    except Exception:
        return False


def _writable(root: Path) -> bool:
    try:
        target = root / model.DATA_DIR
        target.mkdir(parents=True, exist_ok=True)
        probe = target / ".write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def _scan_text(summary: dict[str, Any], facts: dict[str, Any]) -> str:
    lines = [
        f"PROJECT {facts.get('name')} · {summary.get('files')} files · {summary.get('bytes')} bytes · {summary.get('scan_ms')} ms",
        f"manifests {summary.get('manifests')} · entrypoints {summary.get('entrypoints')} · services {summary.get('services')} · routes {summary.get('routes')} · docs {summary.get('docs')}",
    ]
    if (facts.get("vcs") or {}).get("type"):
        vcs = facts["vcs"]
        lines.append(f"git {vcs.get('branch')} @ {vcs.get('head')} · {len(vcs.get('commits') or [])} commits")
    lines.append("")
    lines.append("ТОП:")
    for entry in (facts.get("inventory") or {}).get("top") or []:
        kind = "dir" if entry.get("dir") else "file"
        lines.append(f"  {entry.get('path')}  ({kind}, {entry.get('files')} files{' · repo' if entry.get('repo') else ''})")
    if facts.get("manifests"):
        lines.append("")
        lines.append("МАНИФЕСТЫ:")
        for man in facts["manifests"][:12]:
            lines.append(f"  {man.get('path')} [{man.get('kind')}] {man.get('name')}")
    if facts.get("entrypoints"):
        lines.append("")
        lines.append("ВХОДЫ:")
        for ep in facts["entrypoints"][:12]:
            lines.append(f"  {ep.get('path')} ({ep.get('kind')})")
    if facts.get("services"):
        lines.append("")
        lines.append("СЕРВИСЫ:")
        for svc in facts["services"][:12]:
            lines.append(f"  {svc.get('name')} → {svc.get('path')}")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())

"""Agent tool handlers.  Every handler returns JSON and never raises."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .atlas_core import build as build_mod
from .atlas_core import facts as facts_mod
from .atlas_core import model, render, structure

_CTX: Any = None
_CFG = build_mod.BuildConfig()


def configure(ctx: Any, cfg: build_mod.BuildConfig | None = None) -> None:
    global _CTX, _CFG
    _CTX = ctx
    if cfg is not None:
        _CFG = cfg


def _json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _project(args: dict[str, Any]) -> Path:
    raw = str(args.get("project_path") or "").strip()
    if not raw:
        raise ValueError("project_path is required")
    root = Path(raw).expanduser()
    if not root.is_absolute():
        raise ValueError("project_path must be absolute")
    root = root.resolve()
    if not root.is_dir():
        raise ValueError(f"not a directory: {root}")
    return root


def _scope(args: dict[str, Any]) -> str:
    scope = str(args.get("scope") or "stale").strip()
    if scope not in {"stale", "all", "undescribed"} and not scope.startswith("node:"):
        raise ValueError("scope must be stale, all, undescribed or node:<id>")
    return scope


def _load_or_scan(root: Path) -> dict[str, Any]:
    facts = model.read_json(model.facts_path(root), None)
    if not facts:
        facts = facts_mod.scan(root)
    return facts


def atlas_scan(args: dict[str, Any], **kwargs: Any) -> str:
    try:
        root = _project(args)
        facts = facts_mod.scan(root)
        return _json({
            "ok": True,
            "root": str(root),
            "facts": str(model.facts_path(root)),
            **render.facts_summary(facts),
            "top": (facts.get("inventory") or {}).get("top") or [],
            "manifests": [{"path": m.get("path"), "kind": m.get("kind"), "name": m.get("name")} for m in (facts.get("manifests") or [])[:12]],
            "entrypoints": facts.get("entrypoints") or [],
            "services": facts.get("services") or [],
        })
    except Exception as exc:  # noqa: BLE001
        return _json({"ok": False, "error": str(exc)})


def atlas_plan(args: dict[str, Any], **kwargs: Any) -> str:
    try:
        root = _project(args)
        scope = _scope(args)
        facts = _load_or_scan(root)
        tree = model.load_tree(root) or structure.propose(facts, root, max_branches=_CFG.max_branches, max_depth=_CFG.max_depth)
        payload = build_mod.plan(root, tree, facts, scope, _CFG)
        return _json({"ok": True, **payload})
    except Exception as exc:  # noqa: BLE001
        return _json({"ok": False, "error": str(exc)})


def atlas_build(args: dict[str, Any], **kwargs: Any) -> str:
    try:
        root = _project(args)
        scope = _scope(args)
        facts = _load_or_scan(root)
        tree = model.load_tree(root)
        if tree is None:
            tree = structure.propose(facts, root, max_branches=_CFG.max_branches, max_depth=_CFG.max_depth)
        confirm = str(args.get("confirm") or "").strip() or None
        planned = None
        if scope in {"all", "undescribed"}:
            planned = build_mod.plan(root, tree, facts, scope, _CFG)
            if len(planned.get("nodes") or []) > 1 and confirm != planned["hash"]:
                return _json({
                    "ok": False,
                    "needs_confirmation": True,
                    "plan": planned,
                    "hint": "Show the plan to the user, then call atlas_build again with confirm=<hash>.",
                })
        result = build_mod.build(
            root,
            llm=(_CTX.llm if _CTX is not None else None),
            scope=scope,
            cfg=_CFG,
            deep=bool(args.get("deep")),
            confirm=confirm or (planned or {}).get("hash"),
        )
        updated = model.load_tree(root)
        return _json({
            "ok": True,
            **result,
            "tree_text": render.tree_text(updated) if updated else "",
        })
    except Exception as exc:  # noqa: BLE001
        return _json({"ok": False, "error": str(exc)})


def atlas_status(args: dict[str, Any], **kwargs: Any) -> str:
    try:
        root = _project(args)
        node_id = str(args.get("node_id") or "").strip()
        tree = model.load_tree(root)
        if tree is None:
            return _json({"ok": True, "exists": False, "root": str(root), "hint": "Run atlas_scan then atlas_build."})
        if node_id:
            return _json({"ok": True, "exists": True, **(render.page_payload(tree, node_id))})
        state = render.state_payload(root, with_stale=True)
        return _json({
            "ok": True,
            "exists": True,
            "project": state["project"],
            "coverage": state["coverage"],
            "stale": sorted(state["stale"]),
            "orphans": state["orphans"],
            "tree_text": render.tree_text(tree),
            "facts": state["facts"],
        })
    except Exception as exc:  # noqa: BLE001
        return _json({"ok": False, "error": str(exc)})


HANDLERS = {
    "atlas_scan": atlas_scan,
    "atlas_plan": atlas_plan,
    "atlas_build": atlas_build,
    "atlas_status": atlas_status,
}

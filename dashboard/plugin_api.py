"""Project Atlas backend, mounted at /api/plugins/atlas/.

The desktop half never touches the filesystem itself: it calls these routes.  A
build runs in a background thread and reports progress through ``/job/<id>`` so
the gateway's RPC loop is never blocked by a model call.

Imported by the gateway at startup, so nothing here may touch the network or the
filesystem at import time.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from atlas_core import build as build_mod  # noqa: E402
from atlas_core import facts as facts_mod  # noqa: E402
from atlas_core import jobs, model, render, structure  # noqa: E402

router = APIRouter()


def _project(raw: str) -> Path:
    if not raw or not str(raw).strip():
        raise HTTPException(400, "path is required")
    root = Path(str(raw)).expanduser()
    if not root.is_absolute():
        raise HTTPException(400, "path must be absolute")
    root = root.resolve()
    if not root.is_dir():
        raise HTTPException(400, f"not a directory: {root}")
    return root


def _cfg() -> build_mod.BuildConfig:
    try:
        from hermes_cli.config import load_config_readonly

        data = load_config_readonly() or {}
        settings = (((data.get("plugins") or {}).get("entries") or {}).get("atlas") or {}).get("settings") or {}
        return build_mod.BuildConfig.from_dict(settings)
    except Exception:
        return build_mod.BuildConfig()


def _llm() -> Any:
    try:
        from agent.plugin_llm import PluginLlm

        return PluginLlm(plugin_id="atlas")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(503, f"model access is unavailable in this process: {exc}")


def _scan_body(body: dict[str, Any]) -> Path:
    return _project(str(body.get("path") or ""))


@router.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "plugin": "atlas", "version": "0.1.0"}


@router.get("/state")
def state(path: str = Query(...), stale: bool = Query(True)) -> dict[str, Any]:
    root = _project(path)
    try:
        return render.state_payload(root, with_stale=bool(stale))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"cannot read atlas.yaml: {exc}") from exc


@router.get("/candidates")
def candidates(root: str = Query(...)) -> dict[str, Any]:
    base = _project(root)
    try:
        return {"ok": True, "root": str(base), "items": facts_mod.candidates(base)}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, str(exc)) from exc


@router.post("/scan")
def scan(body: dict[str, Any]) -> dict[str, Any]:
    root = _scan_body(body)
    try:
        facts = facts_mod.scan(root)
        return {"ok": True, "root": str(root), **render.facts_summary(facts)}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"scan failed: {exc}") from exc


@router.post("/plan")
def plan(body: dict[str, Any]) -> dict[str, Any]:
    root = _scan_body(body)
    scope = str(body.get("scope") or "stale")
    cfg = _cfg()
    try:
        facts = model.read_json(model.facts_path(root), None) or facts_mod.scan(root)
        tree = model.load_tree(root) or structure.propose(facts, root, max_branches=cfg.max_branches, max_depth=cfg.max_depth)
        return {"ok": True, **build_mod.plan(root, tree, facts, scope, cfg)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"plan failed: {exc}") from exc


@router.post("/build")
def build(body: dict[str, Any]) -> dict[str, Any]:
    root = _scan_body(body)
    scope = str(body.get("scope") or "stale")
    cfg = _cfg()
    confirm = str(body.get("confirm") or "").strip() or None
    deep = bool(body.get("deep"))
    try:
        facts = model.read_json(model.facts_path(root), None) or facts_mod.scan(root)
        tree = model.load_tree(root) or structure.propose(facts, root, max_branches=cfg.max_branches, max_depth=cfg.max_depth)
        planned = None
        if scope in {"all", "undescribed"}:
            planned = build_mod.plan(root, tree, facts, scope, cfg)
            if len(planned.get("nodes") or []) > 1 and confirm != planned["hash"]:
                raise HTTPException(409, {"needs_confirmation": True, "plan": planned})
        job = jobs.start(
            str(root),
            scope,
            lambda running_job: build_mod.build(
                root,
                llm=_llm(),
                scope=scope,
                cfg=cfg,
                deep=deep,
                confirm=confirm or (planned or {}).get("hash"),
                progress=jobs.progress_for(running_job),
            ),
        )
        return {"ok": True, "job": job, "plan": planned}
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"build failed to start: {exc}") from exc


@router.get("/job/{job_id}")
def job(job_id: str) -> dict[str, Any]:
    payload = jobs.get(job_id)
    if payload is None:
        raise HTTPException(404, "unknown job")
    return {"ok": True, "job": payload}


@router.get("/job")
def last_job(path: str = Query(...)) -> dict[str, Any]:
    root = _project(path)
    payload = jobs.latest(str(root))
    return {"ok": True, "job": payload}


@router.post("/lock")
def lock(body: dict[str, Any]) -> dict[str, Any]:
    root = _scan_body(body)
    node_id = str(body.get("node_id") or "").strip()
    if not node_id:
        raise HTTPException(400, "node_id is required")
    try:
        tree = model.load_tree_raw(root)
        if tree is None:
            raise HTTPException(404, "atlas.yaml does not exist yet")
        index = model.by_id(tree.get("nodes") or [])
        node = index.get(node_id)
        if node is None:
            raise HTTPException(404, f"unknown node {node_id!r}")
        node["locked"] = bool(body.get("locked"))
        model.save_tree_raw(root, tree)
        return {"ok": True, "node": model.plain(node)}
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"lock failed: {exc}") from exc


@router.get("/changelog")
def changelog(path: str = Query(...), limit: int = Query(80)) -> dict[str, Any]:
    root = _project(path)
    target = model.changelog_path(root)
    try:
        if not target.exists():
            return {"ok": True, "text": ""}
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        return {"ok": True, "text": "\n".join(lines[-max(1, min(limit, 400)):])}
    except OSError as exc:
        raise HTTPException(500, str(exc)) from exc

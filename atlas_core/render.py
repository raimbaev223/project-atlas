"""Read-only projections used by the desktop page.

The renderer knows nothing about a project: it asks for a tree and gets exactly
what ``atlas.yaml`` says, plus two computed facts — coverage and which nodes'
code moved since the last build.  Delete every page file and these functions
still produce the Documentation tab.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import facts as facts_mod
from . import model


def facts_summary(facts: dict[str, Any] | None) -> dict[str, Any]:
    if not facts:
        return {}
    inv = facts.get("inventory") or {}
    return {
        "scanned_at": facts.get("scanned_at"),
        "root": facts.get("root"),
        "files": inv.get("total", 0),
        "bytes": inv.get("bytes", 0),
        "manifests": len(facts.get("manifests") or []),
        "entrypoints": len(facts.get("entrypoints") or []),
        "services": len(facts.get("services") or []),
        "routes": len(facts.get("routes") or []),
        "docs": len(facts.get("docs") or []),
        "todos": len(facts.get("todos") or []),
        "git": bool((facts.get("vcs") or {}).get("type")),
        "branch": (facts.get("vcs") or {}).get("branch"),
        "warnings": facts.get("warnings") or [],
        "scan_ms": facts.get("scan_ms"),
    }


def stale_map(root: str | Path, tree: dict[str, Any]) -> dict[str, str]:
    """node id -> current hash, for nodes whose files changed."""
    base = Path(root).expanduser().resolve()
    cache = model.cache_path(base)
    out: dict[str, str] = {}
    for node in tree.get("nodes") or []:
        current = facts_mod.hash_paths(base, node.get("paths") or [], cache_file=cache)
        if current.get("hash") != node.get("sourceHash"):
            out[node["id"]] = current.get("hash") or ""
    return out


def state_payload(root: str | Path, *, with_stale: bool = True) -> dict[str, Any]:
    base = Path(root).expanduser().resolve()
    tree = model.load_tree(base)
    facts = model.read_json(model.facts_path(base), None)
    if tree is None:
        return {
            "ok": True,
            "project": base.name,
            "root": str(base),
            "exists": False,
            "nodes": [],
            "orphans": [],
            "coverage": {"total": 0, "full": 0, "partial": 0, "none": 0},
            "facts": facts_summary(facts),
            "stale": {},
        }
    nodes = tree.get("nodes") or []
    stale = stale_map(base, tree) if with_stale else {}
    return {
        "ok": True,
        "project": tree.get("project") or base.name,
        "root": str(base),
        "exists": True,
        "version": tree.get("version"),
        "generated": tree.get("generated"),
        "generator": tree.get("generator"),
        "nodes": nodes,
        "orphans": tree.get("orphans") or [],
        "coverage": model.coverage(nodes),
        "facts": facts_summary(facts),
        "stale": stale,
        "paths_ok": _paths_ok(base, nodes),
    }


def _paths_ok(base: Path, nodes: list[dict[str, Any]]) -> dict[str, bool]:
    out = {}
    for node in nodes:
        ok = True
        for rel in node.get("paths") or []:
            target = (base / rel).resolve()
            if not (target == base or (base in target.parents and target.exists())):
                ok = False
                break
        out[node["id"]] = ok
    return out


def page_payload(tree: dict[str, Any], node_id: str) -> dict[str, Any]:
    nodes = tree.get("nodes") or []
    index = model.by_id(nodes)
    node = index.get(node_id)
    if not node:
        raise KeyError(node_id)
    children = model.children_map(nodes).get(node_id, [])
    links = []
    for link in node.get("links") or []:
        target = index.get(link.get("to"))
        links.append({
            "to": link.get("to"),
            "type": link.get("type"),
            "name": target.get("name") if target else link.get("to"),
        })
    backlinks = [
        {"from": other["id"], "name": other.get("name"), "type": link.get("type")}
        for other in nodes for link in (other.get("links") or [])
        if link.get("to") == node_id
    ]
    return {
        "node": node,
        "breadcrumb": [
            {"id": item["id"], "name": item.get("name")}
            for item in model.breadcrumb(node, index)
        ],
        "children": [{"id": c["id"], "name": c.get("name"), "status": c.get("status")} for c in children],
        "links": links,
        "backlinks": backlinks,
        "depth": len(model.breadcrumb(node, index)),
    }


def tree_text(tree: dict[str, Any]) -> str:
    """Plain-text tree for the agent tool and the CLI."""
    children = model.children_map(tree.get("nodes") or [])
    lines = [f"{tree.get('project')}  ({model.coverage(tree.get('nodes') or [])['total']} узлов)"]

    def walk(parent: str, prefix: str) -> None:
        items = children.get(parent, [])
        for i, node in enumerate(items):
            last = i == len(items) - 1
            status = {"full": "●", "partial": "◐", "none": "○"}.get(node.get("status"), "○")
            lock = " 🔒" if node.get("locked") else ""
            paths = ", ".join(node.get("paths") or [])
            lines.append(f"{prefix}{'└─' if last else '├─'} {status} {node.get('name')} [{node.get('id')}] {paths}{lock}")
            walk(node["id"], prefix + ("   " if last else "│  "))

    walk("root", "")
    for orphan in (tree.get("orphans") or [])[:12]:
        lines.append(f"… сирота: {orphan.get('path')} → {orphan.get('suggestParent') or 'root'}")
    return "\n".join(lines)

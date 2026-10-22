"""Turn scanner facts into a draft tree.

This is the part that must work for a Vite SPA, a Python CLI and a monorepo
without project-specific code.  It only reads facts and the filesystem shape; a
model may refine the result, but the draft alone is already a valid map (every
node has a real path, every uncovered top-level folder is reported as an orphan).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from . import facts as facts_mod
from . import model


@dataclass
class Candidate:
    name: str
    paths: list[str]
    kind: str = "other"
    weight: int = 0
    evidence: list[str] = field(default_factory=list)
    children: list["Candidate"] = field(default_factory=list)
    summary: str = ""


_ACRONYMS = {"api": "API", "ui": "UI", "cli": "CLI", "db": "DB", "http": "HTTP", "id": "ID"}


def humanize(text: str) -> str:
    words = [w for w in re.split(r"[_\-\s]+", (text or "").strip()) if w]
    if not words:
        return text
    out = []
    for word in words:
        lower = word.lower()
        out.append(_ACRONYMS.get(lower, lower[:1].upper() + lower[1:]))
    return " ".join(out)[:80]


def _norm(path: str) -> str:
    value = (path or ".").replace("\\", "/").strip()
    while value.startswith("./"):
        value = value[2:]
    value = value.rstrip("/")
    return value or "."


def _exists(root: Path, rel: str) -> bool:
    target = (root / rel).resolve()
    return target == root or (root in target.parents and target.exists())


def _files_under(facts: dict[str, Any], rel: str) -> int:
    rel = _norm(rel)
    if rel == ".":
        return int((facts.get("inventory") or {}).get("total") or 0)
    best = 0
    for item in (facts.get("inventory") or {}).get("dirs") or []:
        path = item.get("path") or ""
        if path == rel or path.startswith(rel + "/"):
            best = max(best, int(item.get("files") or 0))
    return best


def _kind_for_path(rel: str, facts: dict[str, Any]) -> str:
    lower = rel.lower()
    name = Path(rel).name.lower()
    if any(token in lower for token in ("deploy", "infra", "k8s", "docker", "helm", "terraform")):
        return "infra"
    if name in {"dashboard", "desktop", "web", "frontend", "ui", "app"}:
        return "ui"
    if name in {"api", "server", "backend", "service", "gateway", "bot"}:
        return "service"
    if name in {"migrations", "db", "database", "models", "store", "storage"}:
        return "data"
    if name in {"cli", "bin", "cmd", "scripts"}:
        return "interface"
    if name in {"lib", "libs", "core", "internal", "pkg", "package", "src"}:
        return "library"
    if name in {"tests", "test", "spec"}:
        return "tool"
    if lower.endswith((".py", ".ts", ".tsx", ".js")) and "/" not in lower:
        return "interface"
    return "other"


def _expand_workspaces(manifest: dict[str, Any], root: Path) -> list[str]:
    base = Path(manifest.get("path") or "").parent
    out: list[str] = []
    for pattern in manifest.get("workspaces") or []:
        pattern = str(pattern).replace("\\", "/").strip()
        if not pattern:
            continue
        if any(ch in pattern for ch in ("*", "?", "[")):
            for match in sorted(root.glob((base / pattern).as_posix()))[:40]:
                if match.is_dir():
                    out.append(match.relative_to(root).as_posix())
        else:
            rel = _norm((base / pattern).as_posix())
            if _exists(root, rel):
                out.append(rel)
    return out


def _compose_service_path(manifest: dict[str, Any], service: dict[str, Any]) -> str:
    base = Path(manifest.get("path") or "").parent
    context = _norm(str(service.get("path") or "."))
    rel = _norm((base / context).as_posix())
    return rel


def raw_candidates(facts: dict[str, Any], root: Path, max_depth: int = 4) -> list[Candidate]:
    out: list[Candidate] = []
    seen_paths: set[str] = set()

    def add(name: str, paths: list[str], kind: str | None = None, evidence: Iterable[str] = ()) -> None:
        cleaned = []
        for rel in paths:
            rel = _norm(rel)
            if _exists(root, rel) and rel not in cleaned:
                cleaned.append(rel)
        if not cleaned:
            return
        if any(p in seen_paths for p in cleaned):
            return
        seen_paths.update(cleaned)
        out.append(Candidate(
            name=name.strip()[:80] or Path(cleaned[0]).name,
            paths=cleaned,
            kind=kind or _kind_for_path(cleaned[0], facts),
            weight=_files_under(facts, cleaned[0]),
            evidence=[str(e)[:120] for e in evidence][:6],
        ))

    workspace_rels: set[str] = set()
    for man in facts.get("manifests") or []:
        workspace_rels.update(_expand_workspaces(man, root))

    # 1. docker-compose services (a service is a process, so it is a branch).
    #    A root-context service in a workspace monorepo is the deployment wrapper,
    #    not a branch — its packages are the branches.
    for man in facts.get("manifests") or []:
        for service in man.get("services") or []:
            rel = _compose_service_path(man, service)
            if rel == "." and len(workspace_rels) >= 2:
                continue
            add(str(service.get("name")), [rel], "service", [f"compose: {man.get('path')}"])

    # 2. workspace packages / sub-projects that carry their own manifest
    for index, man in enumerate(facts.get("manifests") or []):
        for workspace in _expand_workspaces(man, root):
            add(Path(workspace).name, [workspace], None, [f"workspace: {man.get('path')}"])
    for entry in (facts.get("inventory") or {}).get("top") or []:
        if not entry.get("dir") or not entry.get("manifest"):
            continue
        if entry.get("path") in {Path(m.get("path") or "").parent.as_posix() for m in facts.get("manifests") or []}:
            continue
        add(entry["name"], [entry["path"]], None, [f"manifest: {entry['manifest']}"])

    # 3. language package directories (Python/Rust/Go)
    for entry in (facts.get("inventory") or {}).get("top") or []:
        rel = entry.get("path") or ""
        if not entry.get("dir"):
            continue
        if entry.get("manifest") or rel in seen_paths:
            continue
        if (root / rel / "__init__.py").exists() or (root / rel / "src" / "lib.rs").exists():
            if Path(rel).name not in {"tests", "test", "docs", "examples"}:
                add(entry["name"], [rel], None, ["language package"])

    # 3b. a single-package project keeps its root modules as one branch
    try:
        root_modules = sorted(
            p.name for p in root.iterdir()
            if p.is_file() and p.suffix in {".py", ".ts", ".js"}
            and not facts_mod._is_secret(p.name)
        )
    except OSError:
        root_modules = []
    if len(root_modules) >= 2 and (root / "__init__.py").exists():
        add("core", root_modules[:10], "library", ["root modules"])
        if out:
            out[-1].weight = max(out[-1].weight, len(root_modules) * 2)

    # 4. feature folders under a conventional source root
    for src in ("src", "app", "packages", "apps", "services", "libs"):
        if not (root / src).is_dir():
            continue
        try:
            children = sorted(p for p in (root / src).iterdir() if p.is_dir())
        except OSError:
            continue
        for child in children:
            rel = f"{src}/{child.name}"
            if child.name.startswith(".") or child.name in {"__pycache__", "node_modules", "assets", "public"}:
                continue
            count = _files_under(facts, rel)
            if count < 3:
                continue
            if any(rel == p or rel.startswith(p + "/") for p in seen_paths):
                continue
            kind = _kind_for_path(child.name, facts)
            if kind == "other" and src in {"src", "app"}:
                kind = "ui" if any((child / n).exists() for n in ("components", "pages", "views")) else "library"
            add(child.name, [rel], kind, [f"{count} files under {src}/"])

    # 5. well-known side folders that are real features in plugin-shaped repos
    for name, kind in (("dashboard", "service"), ("desktop", "ui"), ("skills", "library"),
                       ("deploy", "infra"), ("infra", "infra"), ("migrations", "data")):
        rel = name
        if (root / rel).is_dir() and _files_under(facts, rel) >= 1 and rel not in seen_paths:
            add(name, [rel], kind, ["conventional folder"])

    # 5b. a launcher outside every package is its own branch — but not a frontend bootstrap
    covered = [p for c in out for p in c.paths]
    for entry in (facts.get("entrypoints") or [])[:10]:
        rel = _norm(entry.get("path") or "")
        if not rel or any(rel == p or rel.startswith(p + "/") for p in covered):
            continue
        if entry.get("kind") != "bin" and "/" in rel:
            continue
        add(f"CLI ({Path(rel).name})", [rel], "interface", [f"entrypoint: {rel}"])
        covered.append(rel)

    # 6. CLI / single entrypoint project: build the branch from the entrypoint
    if not out:
        entrypoints = facts.get("entrypoints") or []
        if entrypoints:
            first = entrypoints[0]
            parent = Path(first["path"]).parent.as_posix()
            if parent == ".":
                parent = "."
            add("cli", [first["path"]], "interface", [f"entrypoint: {first['path']}"])
            for entry in (facts.get("inventory") or {}).get("top") or []:
                rel = entry.get("path") or ""
                if entry.get("dir") and rel not in {".", "tests"} and (root / rel / "__init__.py").exists():
                    add(rel.replace("_", " "), [rel], "library", ["package directory"])
                    break
        else:
            add(root.name, ["."], "other", ["project root"])

    if not out:
        add(root.name, ["."], "other", ["project root"])

    # children: one level of meaningful subdirectories, capped and only with evidence
    for candidate in out:
        if len(candidate.children) >= 4:
            continue
        base_rel = candidate.paths[0]
        target = root / base_rel
        if not target.is_dir():
            continue
        try:
            subdirs = sorted(p for p in target.iterdir() if p.is_dir())
        except OSError:
            continue
        for sub in subdirs:
            name = sub.name.lower()
            if name.startswith(".") or name in {"node_modules", "__pycache__", "assets", "public", "static",
                                                "src", "tests", "test", "__tests__", "docs", "dist", "build",
                                                "coverage", "scripts", "migrations"}:
                continue
            if name not in {"components", "pages", "features", "modules", "api", "server", "client",
                            "store", "stores", "hooks", "routes", "services", "handlers", "workers",
                            "commands", "models", "ui", "views"}:
                continue
            rel = _norm(f"{base_rel}/{sub.name}")
            count = _files_under(facts, rel)
            parent_count = max(1, _files_under(facts, base_rel))
            if count < 8 or count > parent_count * 0.85:
                continue
            kind = _kind_for_path(sub.name, facts)
            if kind == "other":
                kind = "library"
            candidate.children.append(Candidate(sub.name, [rel], kind, count, [f"{count} files"]))
            if len(candidate.children) >= 4:
                break
    return out


def _flatten(candidate: Candidate, parent: str, depth: int, used: set[str], root: Path,
             out: list[dict[str, Any]], max_depth: int) -> None:
    nid = model.unique_id(candidate.name, used)
    hashed = facts_mod.hash_paths(root, candidate.paths) if candidate.paths else {"hash": "0" * 12}
    out.append({
        "id": nid,
        "name": humanize(candidate.name),
        "parent": parent,
        "kind": candidate.kind if candidate.kind in model.KINDS else "other",
        "summary": candidate.summary[:240],
        "status": "none",
        "confidence": 0.2,
        "locked": False,
        "paths": candidate.paths,
        "sourceHash": hashed.get("hash") or "0" * 12,
        "decisions": [],
        "links": [],
        "todo": [],
    })
    if depth >= max_depth:
        return
    for child in candidate.children[:4]:
        _flatten(child, nid, depth + 1, used, root, out, max_depth)


def propose(facts: dict[str, Any], root: str | Path, *, max_branches: int = 8, max_depth: int = 4) -> dict[str, Any]:
    base = Path(root).expanduser().resolve()
    candidates = raw_candidates(facts, base, max_depth=max_depth)
    candidates.sort(key=lambda c: (-c.weight, c.name.lower()))
    if len(candidates) > max_branches:
        keep = candidates[: max(1, max_branches - 1)]
        rest = candidates[max(1, max_branches - 1):]
        common = _common_root([c.paths[0] for c in rest]) or "."
        grouped = Candidate(
            "Прочее",
            [common],
            "other",
            sum(c.weight for c in rest),
            [f"{len(rest)} групп: " + ", ".join(c.name for c in rest[:8])],
            summary="Группа из " + str(len(rest)) + " частей: " + ", ".join(c.name for c in rest[:8]),
        )
        candidates = keep + [grouped]

    used: set[str] = set()
    nodes: list[dict[str, Any]] = []
    for candidate in candidates:
        _flatten(candidate, "root", 1, used, base, nodes, max_depth)

    tree = model.new_tree(base.name)
    tree["nodes"] = nodes
    tree["orphans"] = find_orphans(tree, facts, base)
    return tree


def _common_root(paths: list[str]) -> str:
    split = [p.split("/") for p in paths if p and p != "."]
    if not split:
        return "."
    prefix = split[0]
    for parts in split[1:]:
        i = 0
        while i < len(prefix) and i < len(parts) and prefix[i] == parts[i]:
            i += 1
        prefix = prefix[:i]
        if not prefix:
            return "."
    return "/".join(prefix) or "."


# ─── merge with an existing (possibly hand-edited) tree ──────────────────────


def _covered(rel: str, nodes: Iterable[dict[str, Any]]) -> bool:
    rel = _norm(rel)
    for node in nodes:
        for path in node.get("paths") or []:
            path = _norm(path)
            if path == "." or rel == path or rel.startswith(path + "/") or path.startswith(rel + "/"):
                return True
    return False


def find_orphans(tree: dict[str, Any], facts: dict[str, Any], root: str | Path) -> list[dict[str, Any]]:
    base = Path(root).expanduser().resolve()
    nodes = tree.get("nodes") or []
    out: list[dict[str, Any]] = []
    for entry in (facts.get("inventory") or {}).get("top") or []:
        rel = entry.get("path") or ""
        if not entry.get("dir"):
            continue
        if not rel or rel.startswith(".") or rel in {"node_modules", "dist", "build"}:
            continue
        if _covered(rel, nodes):
            continue
        out.append({
            "path": rel,
            "files": int(entry.get("files") or 0),
            "suggestParent": _suggest_parent(rel, nodes),
            "reason": _orphan_reason(rel, entry),
        })
        if len(out) >= 40:
            break
    return out


def _suggest_parent(rel: str, nodes: list[dict[str, Any]]) -> str:
    name = Path(rel).name.lower()
    if name in {"docs", "doc"}:
        return "root"
    hints = {
        "scripts": ("interface", "root"),
        "tests": ("tool", "root"), "test": ("tool", "root"), "deploy": ("infra", "root"),
        "docker": ("infra", "root"), "assets": ("ui", "root"), "public": ("ui", "root"),
        "migrations": ("data", "root"), "examples": ("library", "root"),
    }
    if name in hints:
        kind, _ = hints[name]
        for node in nodes:
            if node.get("kind") == kind:
                return str(node.get("id"))
    best = None
    best_score = 0
    for node in nodes:
        for path in node.get("paths") or []:
            path = _norm(path)
            if path == ".":
                continue
            shared = len(Path(path).parts) if rel.startswith(path) else 0
            if shared > best_score:
                best_score = shared
                best = node
    return str(best.get("id")) if best else "root"


def _orphan_reason(rel: str, entry: dict[str, Any]) -> str:
    if entry.get("dir"):
        return f"папка вне карты · {entry.get('files', 0)} файлов"
    return "файл в корне проекта не отнесён ни к одному узлу"


def merge(existing: dict[str, Any] | None, proposed: dict[str, Any], root: str | Path) -> tuple[dict[str, Any], dict[str, list[str]]]:
    """Keep every existing node; add, never silently reparent or delete."""
    changes: dict[str, list[str]] = {"added": [], "kept": [], "missing": []}
    if not existing or not existing.get("nodes"):
        return proposed, {"added": [n["id"] for n in proposed.get("nodes") or []], "kept": [], "missing": []}

    tree = existing
    nodes = tree.setdefault("nodes", [])
    index = {n.get("id"): n for n in nodes if isinstance(n, dict)}
    used_ids = {nid for nid in index if isinstance(nid, str)}
    base = Path(root).expanduser().resolve()
    covered_paths = []
    for node in nodes:
        covered_paths.extend(_norm(p) for p in (node.get("paths") or []))

    for candidate in proposed.get("nodes") or []:
        cid = candidate.get("id")
        path = _norm((candidate.get("paths") or ["."])[0])
        match = index.get(cid)
        if match is None:
            # a path that already belongs to an existing node is not a new branch
            if any(path == p or path.startswith(p + "/") or p.startswith(path + "/") for p in covered_paths):
                match = next(
                    (n for n in nodes if any(path == _norm(p) or path.startswith(_norm(p) + "/") for p in n.get("paths") or [])),
                    None,
                )
        if match is not None:
            changes["kept"].append(str(match.get("id")))
            # never change parent/name/paths/locked on an existing node
            continue
        new_id = model.unique_id(candidate.get("name") or cid or "node", used_ids)
        node = dict(candidate)
        node["id"] = new_id
        node["parent"] = "root" if candidate.get("parent") == "root" else candidate.get("parent")
        nodes.append(node)
        index[new_id] = node
        covered_paths.append(path)
        changes["added"].append(new_id)

    for node in nodes:
        missing = [rel for rel in node.get("paths") or [] if not _exists(base, rel)]
        if missing and not node.get("locked"):
            node["status"] = "none"
            node["confidence"] = min(float(node.get("confidence") or 0), 0.2)
            note = "Пути узла не найдены в коде: " + ", ".join(missing[:4])
            todo = node.setdefault("todo", [])
            if note not in todo:
                todo.append(note)
            changes["missing"].append(str(node.get("id")))

    tree["orphans"] = proposed.get("orphans") or []
    model.touch_generated(tree)
    return tree, changes

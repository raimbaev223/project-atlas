"""atlas.yaml — the one file the renderer and the updater share.

The file is deliberately boring: YAML, stable key order, no generated pages on
disk.  Everything the Documentation tab shows is derived from ``nodes`` here.

Parsing goes through ``ruamel.yaml`` in round-trip mode so a hand edit keeps its
comments, blank lines and key order when the agent updates a *different* node.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

try:  # runtime dependency (Hermes itself uses it)
    from ruamel.yaml import YAML
except Exception:  # pragma: no cover - only on a broken install
    YAML = None  # type: ignore[assignment]

VERSION = 1
GENERATOR = "project-atlas"
DATA_DIR = ".atlas"
TREE_FILE = "atlas.yaml"
FACTS_FILE = "facts.json"
CHANGELOG_FILE = "changelog.md"
CACHE_DIR = "cache"
JOBS_DIR = "jobs"

KINDS = ("ui", "service", "runtime", "interface", "data", "infra", "library", "tool", "other")
STATUSES = ("full", "partial", "none")
STATUS_LABEL = {"full": "описано полностью", "partial": "описано частично", "none": "без документации"}
KIND_LABEL = {
    "ui": "интерфейс", "service": "сервис", "runtime": "рантайм", "interface": "точка входа",
    "data": "данные", "infra": "инфраструктура", "library": "библиотека", "tool": "инструмент",
    "other": "другое",
}
ID_RE = re.compile(r"^(?!root$)[a-z0-9][a-z0-9-]{0,63}$")
HASH_RE = re.compile(r"^[0-9a-f]{12}$")
COMMIT_RE = re.compile(r"^(?:commit\s+)?[0-9a-f]{7,40}$")


def utcnow() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def data_dir(root: str | Path) -> Path:
    return Path(root).expanduser().resolve() / DATA_DIR


def tree_path(root: str | Path) -> Path:
    return data_dir(root) / TREE_FILE


def facts_path(root: str | Path) -> Path:
    return data_dir(root) / FACTS_FILE


def changelog_path(root: str | Path) -> Path:
    return data_dir(root) / CHANGELOG_FILE


def cache_path(root: str | Path) -> Path:
    return data_dir(root) / CACHE_DIR / "hashes.json"


def _yaml(rt: bool = True):
    if YAML is None:
        raise RuntimeError("ruamel.yaml is required to read or write atlas.yaml")
    y = YAML(typ="rt" if rt else "safe")
    y.preserve_quotes = True
    y.width = 4096
    y.indent(mapping=2, sequence=4, offset=2)
    y.sort_base_mapping_type_on_output = False
    return y


def plain(value: Any) -> Any:
    """CommentedMap/CommentedSeq -> plain dict/list (JSON-safe)."""
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    return value


def load_tree(root: str | Path) -> dict[str, Any] | None:
    path = tree_path(root)
    if not path.is_file():
        return None
    with path.open("r", encoding="utf-8") as fh:
        data = _yaml(rt=True).load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a mapping")
    return plain(data)


def load_tree_roundtrip(root: str | Path):
    path = tree_path(root)
    if not path.is_file():
        return None
    with path.open("r", encoding="utf-8") as fh:
        return _yaml(rt=True).load(fh)


def new_tree(project: str) -> dict[str, Any]:
    return {
        "version": VERSION,
        "project": project,
        "generated": utcnow(),
        "generator": GENERATOR,
        "nodes": [],
        "orphans": [],
    }


def load_tree_raw(root: str | Path):
    """Round-trip object: hand edits and comments survive an agent update."""
    return load_tree_roundtrip(root)


def save_tree(root: str | Path, data: Any) -> Path:
    """Atomic write.  Validates the plain form before replacing the live file."""
    return save_tree_raw(root, data)


def save_tree_raw(root: str | Path, data: Any) -> Path:
    directory = data_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    path = tree_path(root)
    payload = plain(data)
    errors, _warnings = validate(payload, root)
    if errors:
        raise ValueError("atlas.yaml validation failed: " + "; ".join(errors[:6]))
    fd, tmp = tempfile.mkstemp(prefix=".atlas-", suffix=".yaml", dir=str(directory))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            _yaml(rt=True).dump(data, fh)
            fh.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return path


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".atlas-", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=False)
            fh.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def read_json(path: Path, default: Any = None) -> Any:
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


# ─── semantic validation ──────────────────────────────────────────────────────


def validate(data: dict[str, Any], root: str | Path | None = None) -> tuple[list[str], list[str]]:
    """Return (errors, warnings).  JSON Schema first, then the rules it cannot say."""
    errors: list[str] = []
    warnings: list[str] = []

    schema_file = Path(__file__).with_name("schema.json")
    try:
        import jsonschema  # type: ignore

        schema = json.loads(schema_file.read_text(encoding="utf-8"))
        validator = jsonschema.Draft202012Validator(schema)
        for err in sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path)):
            where = ".".join(str(p) for p in err.absolute_path) or "(root)"
            errors.append(f"{where}: {err.message}")
    except ImportError:
        warnings.append("jsonschema is not installed — structural checks only")

    nodes = data.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        errors.append("nodes: at least one node is required")
        return errors, warnings

    seen: dict[str, dict[str, Any]] = {}
    for i, node in enumerate(nodes):
        if not isinstance(node, dict):
            errors.append(f"nodes[{i}]: must be an object")
            continue
        nid = node.get("id")
        if not isinstance(nid, str) or not ID_RE.match(nid):
            errors.append(f"nodes[{i}].id: must match {ID_RE.pattern}")
            continue
        if nid in seen:
            errors.append(f"nodes[{i}].id: duplicate id {nid!r}")
        seen[nid] = node

    parents: dict[str, str | None] = {}
    for nid, node in seen.items():
        parent = node.get("parent")
        if parent in ("root", None):
            parents[nid] = None
        elif isinstance(parent, str) and parent in seen:
            parents[nid] = parent
        else:
            errors.append(f"node {nid!r}: parent {parent!r} does not exist (use 'root' for a top-level node)")
            parents[nid] = None

    # cycles + depth
    for nid in seen:
        chain: list[str] = []
        cur: str | None = nid
        while cur is not None:
            if cur in chain:
                errors.append(f"node {nid!r}: parent cycle via {cur!r}")
                break
            chain.append(cur)
            cur = parents.get(cur)
        else:
            if len(chain) > 4:
                warnings.append(f"node {nid!r}: depth {len(chain)} — the map is allowed to go 4 levels deep")

    for nid, node in seen.items():
        paths = node.get("paths") or []
        if not isinstance(paths, list) or not paths:
            errors.append(f"node {nid!r}: paths must contain at least one project-relative path")
        elif root is not None:
            base = Path(root).expanduser().resolve()
            for rel in paths:
                if not isinstance(rel, str):
                    errors.append(f"node {nid!r}: path must be a string")
                    continue
                candidate = (base / rel).resolve()
                if base not in candidate.parents and candidate != base:
                    errors.append(f"node {nid!r}: path {rel!r} escapes the project")
                elif not candidate.exists():
                    warnings.append(f"node {nid!r}: path {rel!r} does not exist")
            for rel in paths:
                if isinstance(rel, str) and rel.startswith(".atlas"):
                    warnings.append(f"node {nid!r}: references .atlas itself — nodes must point at project code")
        for link in node.get("links") or []:
            target = link.get("to") if isinstance(link, dict) else None
            if target not in seen:
                errors.append(f"node {nid!r}: link target {target!r} does not exist")
            if target == nid:
                errors.append(f"node {nid!r}: link to itself")
        decisions = node.get("decisions") or []
        for j, dec in enumerate(decisions):
            if not isinstance(dec, dict):
                errors.append(f"node {nid!r}.decisions[{j}]: must be an object")
                continue
            for ev in dec.get("evidence") or []:
                if not isinstance(ev, str):
                    errors.append(f"node {nid!r}.decisions[{j}].evidence: must be strings")
                    continue
                if not COMMIT_RE.match(ev) and root is not None:
                    base = Path(root).expanduser().resolve()
                    if not (base / ev).exists() and not any(ch in ev for ch in ("/", ".")):
                        warnings.append(f"node {nid!r}: evidence {ev!r} is neither a commit nor a path")

    if root is not None and isinstance(data.get("project"), str):
        pass
    return errors, warnings


# ─── helpers for the renderer / updater ───────────────────────────────────────


def coverage(nodes: Iterable[dict[str, Any]]) -> dict[str, int]:
    total = full = partial = none = 0
    for node in nodes:
        total += 1
        status = node.get("status")
        if status == "full":
            full += 1
        elif status == "partial":
            partial += 1
        else:
            none += 1
    return {"total": total, "full": full, "partial": partial, "none": none}


def by_id(nodes: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {n["id"]: n for n in nodes if isinstance(n, dict) and "id" in n}


def children_map(nodes: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for node in nodes:
        parent = node.get("parent") or "root"
        out.setdefault(parent, []).append(node)
    return out


def top_level(nodes: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [n for n in nodes if n.get("parent") in (None, "root")]


def breadcrumb(node: dict[str, Any], index: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    chain = [node]
    cur = node
    guard = 0
    while cur.get("parent") not in (None, "root") and guard < 10:
        nxt = index.get(cur.get("parent") or "")
        if not nxt or nxt is cur:
            break
        chain.append(nxt)
        cur = nxt
        guard += 1
    chain.reverse()
    return chain


def descendants(node_id: str, children: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    stack = list(children.get(node_id, []))
    guard = 0
    while stack and guard < 500:
        guard += 1
        node = stack.pop()
        out.append(node)
        stack.extend(children.get(node["id"], []))
    return out


def neighbours(node_id: str, nodes: Iterable[dict[str, Any]]) -> list[str]:
    children = children_map(nodes)
    index = by_id(nodes)
    node = index.get(node_id)
    if not node:
        return []
    ids = {node_id}
    parent = node.get("parent")
    if parent and parent in index:
        ids.add(parent)
    for child in children.get(node_id, []):
        ids.add(child["id"])
    return sorted(ids)


def plan_hash(plan: dict[str, Any]) -> str:
    body = json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(body).hexdigest()[:16]


# ─── ids ─────────────────────────────────────────────────────────────────────


_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh",
    "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "ts",
    "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu",
    "я": "ya",
}


def slugify(text: str, fallback: str = "node") -> str:
    text = (text or "").strip().lower()
    text = "".join(_TRANSLIT.get(ch, ch) for ch in text)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)[:48].strip("-")
    return slug or fallback


def unique_id(base: str, used: set[str]) -> str:
    slug = slugify(base)
    if slug not in used:
        used.add(slug)
        return slug
    for i in range(2, 100):
        candidate = f"{slug}-{i}"
        if candidate not in used:
            used.add(candidate)
            return candidate
    candidate = f"{slug}-{hashlib.sha1(base.encode()).hexdigest()[:6]}"
    used.add(candidate)
    return candidate


# ─── changelog ───────────────────────────────────────────────────────────────


def append_changelog(root: str | Path, title: str, lines: Iterable[str]) -> Path:
    path = changelog_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists()
    with path.open("a", encoding="utf-8") as fh:
        if not exists:
            fh.write("# Project Atlas — changelog\n\n")
        fh.write(f"## {utcnow()} — {title}\n\n")
        for line in lines:
            fh.write(f"- {line}\n")
        fh.write("\n")
    return path


def touch_generated(data: Any) -> None:
    data["generated"] = utcnow()
    data["generator"] = GENERATOR

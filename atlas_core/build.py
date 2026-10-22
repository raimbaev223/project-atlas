"""The agent layer: facts + code excerpts -> atlas.yaml.

One structured model call per node, plus one optional call to shape the branch
list.  The builder is deliberately conservative:

* locked nodes are never written (except a todo note when their code moved);
* every decision must cite an existing path or a commit;
* every path and link is checked before it reaches the file;
* a failed or empty model answer leaves the node ``partial``/``none`` instead of
  inventing prose.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from . import facts as facts_mod
from . import model, structure

DEFAULT_CFG = {
    "max_files_per_node": 10,
    "max_node_chars": 24_000,
    "max_branches": 8,
    "max_depth": 4,
    "max_nodes_per_run": 40,
}

REDACTIONS = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), "[redacted private key]"),
    (re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"), "[redacted aws key]"),
    (re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{20,}\b"), "[redacted api key]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), "[redacted github token]"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), "[redacted slack token]"),
    (re.compile(r"(?i)\b(password|passwd|secret|token|api[_-]?key)\s*[:=]\s*[\"']?[^\s\"']{6,}"), r"\1=[redacted]"),
    (re.compile(r"\b[A-Za-z0-9+/]{60,}={0,2}\b"), "[redacted long token]"),
]

CODE_BONUS = (
    "readme", "index.", "main.", "app.", "cli.", "server.", "routes", "api", "model",
    "schema", "config", "settings", "service", "store", "agent", "core", "dockerfile",
    "compose", "pyproject", "package.json", "cargo.toml", "go.mod",
)


@dataclass
class BuildConfig:
    max_files_per_node: int = DEFAULT_CFG["max_files_per_node"]
    max_node_chars: int = DEFAULT_CFG["max_node_chars"]
    max_branches: int = DEFAULT_CFG["max_branches"]
    max_depth: int = DEFAULT_CFG["max_depth"]
    max_nodes_per_run: int = DEFAULT_CFG["max_nodes_per_run"]

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "BuildConfig":
        data = data or {}
        return cls(
            max_files_per_node=int(data.get("max_files_per_node") or DEFAULT_CFG["max_files_per_node"]),
            max_node_chars=int(data.get("max_node_chars") or DEFAULT_CFG["max_node_chars"]),
            max_branches=int(data.get("max_branches") or DEFAULT_CFG["max_branches"]),
            max_depth=int(data.get("max_depth") or DEFAULT_CFG["max_depth"]),
            max_nodes_per_run=int(data.get("max_nodes_per_run") or DEFAULT_CFG["max_nodes_per_run"]),
        )


class StructuredLlm:
    """Adapter over ``ctx.llm`` / ``PluginLlm``; tests pass a fake with the same method."""

    def __init__(self, llm: Any):
        self.llm = llm
        self.last_text = ""

    def structured(self, *, instructions: str, input_text: str, schema: dict[str, Any],
                   schema_name: str, max_tokens: int, temperature: float = 0.2) -> dict[str, Any] | None:
        result = self.llm.complete_structured(
            instructions=instructions,
            input=[{"type": "text", "text": input_text}],
            json_schema=schema,
            schema_name=schema_name,
            max_tokens=max_tokens,
            temperature=temperature,
            purpose=f"atlas.{schema_name}",
        )
        self.last_text = getattr(result, "text", "") or ""
        parsed = getattr(result, "parsed", None)
        if isinstance(parsed, dict):
            return parsed
        return _extract_json(self.last_text)


def _extract_json(text: str) -> dict[str, Any] | None:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except ValueError:
        pass
    start = text.find("{")
    while start != -1:
        depth = 0
        in_str = False
        escape = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        data = json.loads(text[start:i + 1])
                        return data if isinstance(data, dict) else None
                    except ValueError:
                        break
        start = text.find("{", start + 1)
    return None


def _redact(text: str) -> str:
    for pattern, replacement in REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


# ─── evidence ────────────────────────────────────────────────────────────────


@dataclass
class Evidence:
    text: str
    files: int
    bytes: int
    truncated: bool
    paths: list[str]


def _read_excerpt(path: Path, limit: int) -> str:
    try:
        if path.stat().st_size > 512 * 1024:
            return ""
        raw = path.read_bytes()[: limit + 4096]
    except OSError:
        return ""
    if b"\x00" in raw[:4096]:
        return ""
    text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()
    cut = "\n".join(lines[:200])
    return cut[:limit]


def _rank_file(rel: str) -> tuple[int, int, str]:
    name = Path(rel).name.lower()
    score = 0
    for i, token in enumerate(CODE_BONUS):
        if token in name:
            score = max(score, 100 - i)
    if name in {"readme.md", "readme", "dockerfile", "package.json", "pyproject.toml"}:
        score += 60
    depth = rel.count("/")
    return (-score, depth, rel)


def _node_files(node: dict[str, Any], root: Path, cfg: BuildConfig) -> list[tuple[str, Path]]:
    base = root
    selected: list[tuple[str, Path]] = []
    for rel in node.get("paths") or []:
        target = (base / rel).resolve()
        if base not in target.parents and target != base:
            continue
        if target.is_file():
            selected.append((rel, target))
            continue
        if not target.is_dir():
            continue
        found: list[str] = []
        for dirpath, dirnames, filenames in os.walk(target):
            dirnames[:] = sorted(d for d in dirnames if d not in facts_mod.PRUNE_DIRS and not d.startswith(".atlas"))
            for name in filenames:
                path = Path(dirpath) / name
                if facts_mod._is_secret(path.as_posix()):
                    continue
                if path.suffix.lower() in facts_mod.SKIP_EXTS:
                    continue
                found.append(path.relative_to(base).as_posix())
                if len(found) >= 400:
                    break
            if len(found) >= 400:
                break
        for rel_file in sorted(found, key=_rank_file)[: cfg.max_files_per_node]:
            selected.append((rel_file, base / rel_file))
    # de-duplicate, cap
    seen = set()
    unique = []
    for rel, path in selected:
        if rel in seen:
            continue
        seen.add(rel)
        unique.append((rel, path))
    return unique[: cfg.max_files_per_node]


def _context_files(node: dict[str, Any], base: Path, cfg: BuildConfig) -> list[tuple[str, Path]]:
    """README, manifest and relevant ADRs are evidence even outside the node's paths."""
    out: list[tuple[str, Path]] = []
    candidates = ["README.md", "README", "readme.md"]
    for rel in node.get("paths") or []:
        target = base / rel
        root = target if target.is_dir() else target.parent
        for name in ("README.md", "package.json", "pyproject.toml", "Cargo.toml", "go.mod"):
            candidates.append((root / name).relative_to(base).as_posix())

    # decisions often live in docs/, not in the code folder — look for the node's
    # own words first, then the conventional decision files
    tokens = [t for t in re.split(r"[^a-z0-9]+", f"{node.get('id', '')} {node.get('name', '')} {' '.join(node.get('paths') or [])}".lower()) if len(t) > 3]
    docs_dir = base / "docs"
    if docs_dir.is_dir():
        found = []
        try:
            for path in sorted(docs_dir.rglob("*.md"))[:300]:
                rel = path.relative_to(base).as_posix()
                lower = rel.lower()
                score = 2 if any(token in lower for token in tokens) else 0
                if any(word in lower for word in ("decision", "adr", "architect", "design", "readme")):
                    score += 1
                if score:
                    found.append((score, rel))
        except OSError:
            found = []
        for _score, rel in sorted(found, key=lambda item: (-item[0], item[1]))[:2]:
            candidates.append(rel)

    seen = set()
    for rel in candidates:
        if rel in seen or not (base / rel).is_file():
            continue
        seen.add(rel)
        out.append((rel, base / rel))
        if len(out) >= 5:
            break
    return out


def build_evidence(node: dict[str, Any], root: str | Path, cfg: BuildConfig) -> Evidence:
    base = Path(root).expanduser().resolve()
    files = _context_files(node, base, cfg) + _node_files(node, base, cfg)
    seen_files = set()
    unique_files = []
    for rel, path in files:
        if rel in seen_files:
            continue
        seen_files.add(rel)
        unique_files.append((rel, path))
    files = unique_files[: cfg.max_files_per_node + 3]
    chunks: list[str] = []
    used = 0
    count = 0
    truncated = False
    history = facts_mod.node_history(base, node.get("paths") or [], limit=12)
    if history:
        lines = ["\n--- commits touching this node ---"]
        lines += [f"{c['sha']} {c['date']} {c['subject']}" for c in history]
        block = "\n".join(lines) + "\n"
        chunks.append(block)
        used += len(block)
    for rel, path in files:
        if used >= cfg.max_node_chars:
            truncated = True
            break
        excerpt = _read_excerpt(path, min(8000, cfg.max_node_chars - used))
        if not excerpt.strip():
            continue
        header = f"\n--- {rel} ---\n"
        chunk = header + _redact(excerpt)
        if used + len(chunk) > cfg.max_node_chars:
            chunk = chunk[: max(0, cfg.max_node_chars - used)]
            truncated = True
        chunks.append(chunk)
        used += len(chunk)
        count += 1
    # directory listing when the node is a folder
    listing = []
    for rel in node.get("paths") or []:
        target = (base / rel).resolve()
        if target.is_dir():
            try:
                names = sorted(p.name + ("/" if p.is_dir() else "") for p in target.iterdir() if not p.name.startswith("."))
            except OSError:
                names = []
            listing.append(f"{rel}/: " + ", ".join(names[:60]))
    text = "\n".join(listing) + "\n" + "".join(chunks)
    return Evidence(text=text.strip(), files=count, bytes=used, truncated=truncated, paths=[r for r, _ in files])


# ─── prompts ─────────────────────────────────────────────────────────────────


STRUCTURE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["branches"],
    "properties": {
        "branches": {
            "type": "array", "minItems": 1, "maxItems": 8,
            "items": {
                "type": "object",
                "required": ["name", "kind", "summary", "paths"],
                "properties": {
                    "name": {"type": "string", "maxLength": 80},
                    "kind": {"enum": list(model.KINDS)},
                    "summary": {"type": "string", "maxLength": 240},
                    "paths": {"type": "array", "minItems": 1, "items": {"type": "string"}},
                    "children": {
                        "type": "array", "maxItems": 4,
                        "items": {
                            "type": "object",
                            "required": ["name", "kind", "summary", "paths"],
                            "properties": {
                                "name": {"type": "string", "maxLength": 80},
                                "kind": {"enum": list(model.KINDS)},
                                "summary": {"type": "string", "maxLength": 240},
                                "paths": {"type": "array", "minItems": 1, "items": {"type": "string"}},
                            },
                        },
                    },
                },
            },
        }
    },
}

DOC_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["summary", "overview", "status", "confidence", "kind"],
    "properties": {
        "summary": {"type": "string", "maxLength": 240},
        "overview": {"type": "string", "maxLength": 4000},
        "status": {"enum": ["full", "partial", "none"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "kind": {"enum": list(model.KINDS)},
        "decisions": {
            "type": "array", "maxItems": 8,
            "items": {
                "type": "object",
                "required": ["title", "why", "evidence"],
                "properties": {
                    "title": {"type": "string", "maxLength": 160},
                    "why": {"type": "string", "maxLength": 600},
                    "evidence": {"type": "array", "minItems": 1, "maxItems": 8, "items": {"type": "string", "maxLength": 200}},
                },
            },
        },
        "links": {
            "type": "array", "maxItems": 12,
            "items": {
                "type": "object", "required": ["to", "type"],
                "properties": {"to": {"type": "string", "maxLength": 64}, "type": {"type": "string", "maxLength": 40}},
            },
        },
        "todo": {"type": "array", "maxItems": 10, "items": {"type": "string", "maxLength": 300}},
    },
}

SYSTEM_DOC = (
    "Ты — картограф кодовой базы. По фрагментам кода, README и коммитам опиши ОДИН узел карты проекта.\n"
    "Правила:\n"
    "- Не выдумывай. Каждое утверждение должно подтверждаться фрагментом кода, документом или коммитом.\n"
    "- Если данных мало: status=partial, confidence<=0.45, а недостающее вынеси в todo.\n"
    "- Пиши на языке README и комментариев проекта; если это неочевидно — по-русски.\n"
    "- summary — одна строка до 200 символов. overview — markdown, 2–4 абзаца: что это, как устроено, с чем связано. Без маркетинга.\n"
    "- decisions — 1–3 настоящих архитектурных решения: почему сделано так, а не иначе. Ищи их в README, docs/ADR, комментариях и коммитах. evidence — существующие пути или sha коммита; без evidence решение не возвращай.\n"
    "- links — только реальные зависимости между узлами; to — id из списка соседей.\n"
    "- status=full ставь только если прочитанного достаточно, чтобы описать узел целиком."
)

SYSTEM_STRUCTURE = (
    "Ты — картограф кодовой базы. По фактам сканера предложи верхнеуровневые ветки карты проекта.\n"
    "Правила:\n"
    "- Ветка — то, что запускается/поставляется отдельно (процесс, контейнер, пакет, бот, воркер) или самостоятельная часть продукта.\n"
    "- 1–8 веток. Если частей больше — сгруппируй.\n"
    "- paths — только реально существующие пути из списка кандидатов.\n"
    "- Не подгоняй проект под шаблон. Маленький CLI — это 1–2 ветки, монорепо — по пакетам/сервисам.\n"
    "- children — до 4, только если внутри ветки есть явные самостоятельные части.\n"
    "- Если уверенности нет — не добавляй ветку."
)


def doc_prompt(node: dict[str, Any], facts_digest: str, evidence: Evidence,
               tree: dict[str, Any], orphans: list[dict[str, Any]]) -> str:
    index = model.by_id(tree.get("nodes") or [])
    neighbours = []
    parent = node.get("parent")
    if parent and parent in index:
        neighbours.append(f"{parent} — родитель ({index[parent].get('name')})")
    for child in model.children_map(tree.get("nodes") or []).get(node["id"], []):
        neighbours.append(f"{child['id']} — дочерний узел ({child.get('name')})")
    for link in node.get("links") or []:
        if link.get("to") in index:
            neighbours.append(f"{link['to']} — связь {link.get('type')}")
    all_nodes = [
        f"{n['id']} — {n.get('name')} ({n.get('kind')})"
        for n in tree.get("nodes") or [] if n.get("id") != node["id"]
    ][:30]
    todo = "\n".join(f"- {t}" for t in node.get("todo") or [])
    return (
        f"ПРОЕКТ\n{facts_digest}\n\n"
        f"УЗЕЛ\nid: {node['id']}\nname: {node.get('name')}\nkind: {node.get('kind')}\n"
        f"paths: {', '.join(node.get('paths') or [])}\n"
        f"текущий статус: {node.get('status')} · уверенность {node.get('confidence')}\n"
        f"текущее описание: {node.get('summary') or '(пусто)'}\n"
        f"открытые вопросы:\n{todo or '- нет'}\n\n"
        f"СОСЕДИ (для links)\n" + ("\n".join(f"- {n}" for n in neighbours) or "- нет") + "\n"
        f"ВСЕ УЗЛЫ КАРТЫ (to в links — только из этого списка)\n"
        + ("\n".join(f"- {n}" for n in all_nodes) or "- нет") + "\n\n"
        f"СИРОТЫ (не привязаны к узлам, не описывай их здесь)\n"
        + ("\n".join(f"- {o.get('path')}" for o in orphans[:10]) or "- нет") + "\n\n"
        f"КОД И ДОКУМЕНТАЦИЯ (выжимка)\n{evidence.text}\n\n"
        "ФОРМАТ ОТВЕТА — только JSON, без пояснений:\n"
        '{"summary": "одна строка", "overview": "markdown 2–4 абзаца", '
        '"status": "full|partial|none", "confidence": 0.0, "kind": "ui|service|runtime|interface|data|infra|library|tool|other", '
        '"decisions": [{"title": "…", "why": "…", "evidence": ["путь/или sha"]}], '
        '"links": [{"to": "id-узла", "type": "uses|used-by|depends-on|exposes|shares-data"}], '
        '"todo": ["открытый вопрос"]}\n'
        "decisions и links могут быть пустыми массивами, если подтверждений нет."
    )


def structure_prompt(facts_digest: str, draft: dict[str, Any]) -> str:
    lines = []
    for node in draft.get("nodes") or []:
        lines.append(f"- {node.get('id')}: {node.get('name')} · {node.get('kind')} · paths={', '.join(node.get('paths') or [])}")
    return (
        f"ФАКТЫ\n{facts_digest}\n\n"
        f"ЧЕРНОВЫЕ ВЕТКИ ОТ ЭВРИСТИКИ (можно переименовать, объединить, разбить)\n"
        + ("\n".join(lines) or "- нет") + "\n\n"
        "Верни JSON по схеме: branches[].paths должны существовать в проекте."
    )


# ─── plan ────────────────────────────────────────────────────────────────────


def select_targets(tree: dict[str, Any], scope: str, root: str | Path, facts: dict[str, Any]) -> list[dict[str, Any]]:
    base = Path(root).expanduser().resolve()
    nodes = tree.get("nodes") or []
    index = model.by_id(nodes)
    if scope.startswith("node:"):
        nid = scope.split(":", 1)[1]
        if nid not in index:
            raise ValueError(f"unknown node {nid!r}")
        ids = model.neighbours(nid, nodes)
        return [index[i] for i in ids if i in index]
    if scope == "all":
        return [n for n in nodes]
    if scope == "undescribed":
        return [n for n in nodes if n.get("status") == "none"]
    # stale (default)
    out = []
    cache = model.cache_path(base)
    for node in nodes:
        if node.get("status") == "none":
            out.append(node)
            continue
        current = facts_mod.hash_paths(base, node.get("paths") or [], cache_file=cache)
        if current.get("hash") != node.get("sourceHash"):
            out.append(node)
    return out


def plan(root: str | Path, tree: dict[str, Any], facts: dict[str, Any], scope: str, cfg: BuildConfig) -> dict[str, Any]:
    base = Path(root).expanduser().resolve()
    targets = select_targets(tree, scope, base, facts)[: cfg.max_nodes_per_run]
    files = 0
    total_bytes = 0
    per_node = []
    for node in targets:
        evidence = build_evidence(node, base, cfg)
        files += evidence.files
        total_bytes += evidence.bytes
        per_node.append({
            "id": node["id"], "name": node.get("name"), "status": node.get("status"),
            "locked": bool(node.get("locked")), "files": evidence.files, "bytes": evidence.bytes,
        })
    payload = {
        "project": tree.get("project"),
        "scope": scope,
        "nodes": per_node,
        "files": files,
        "bytes": total_bytes,
        "approx_tokens": int(total_bytes / 4) + 1200 * len(targets),
        "calls": len(targets),
        "warnings": [],
    }
    if any(n["locked"] for n in per_node):
        payload["warnings"].append("Заблокированные узлы будут пропущены.")
    payload["hash"] = model.plan_hash({k: v for k, v in payload.items() if k != "hash"})
    return payload


# ─── writing one node ─────────────────────────────────────────────────────────


def _valid_evidence(evidence: str, root: Path) -> bool:
    evidence = evidence.strip()
    if not evidence:
        return False
    if model.COMMIT_RE.match(evidence):
        return True
    rel = evidence.split(" (", 1)[0].strip().lstrip("./")
    rel = re.sub(r":\d+(?:-\d+)?$", "", rel)
    target = (root / rel).resolve()
    return target == root or (root in target.parents and target.exists())


LINK_TYPES = {
    "uses": "uses", "use": "uses", "depends-on": "depends-on", "depends_on": "depends-on",
    "dependency": "depends-on", "requires": "depends-on", "used-by": "used-by",
    "used_by": "used-by", "exposes": "exposes", "exposed-by": "exposed-by",
    "shares-data": "shares-data", "shares-config": "shares-config", "calls": "calls",
    "contains": "contains",
}


def _normal_link_type(value: Any) -> str:
    raw = str(value or "").strip().lower().replace(" ", "-")
    return LINK_TYPES.get(raw, LINK_TYPES.get(raw.replace("-", "_"), raw[:40] or "depends-on"))


def _apply_doc(node: dict[str, Any], result: dict[str, Any], root: Path, tree: dict[str, Any],
               warnings: list[str]) -> bool:
    index = model.by_id(tree.get("nodes") or [])
    changed = False

    summary = str(result.get("summary") or "").strip()[:240]
    overview = str(result.get("overview") or "").strip()[:8000]
    status = result.get("status") if result.get("status") in model.STATUSES else "partial"
    confidence = result.get("confidence")
    try:
        confidence = max(0.0, min(1.0, float(confidence)))
    except (TypeError, ValueError):
        confidence = 0.3
    kind = result.get("kind") if result.get("kind") in model.KINDS else node.get("kind")

    if not summary:
        status, confidence = "none", min(confidence, 0.2)
    elif status == "full" and len(overview) < 160:
        status, confidence = "partial", min(confidence, 0.6)
    if status == "partial":
        confidence = min(confidence, 0.8)

    decisions = []
    for item in result.get("decisions") or []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()[:160]
        why = str(item.get("why") or "").strip()[:600]
        evidence = [str(e).strip()[:200] for e in (item.get("evidence") or []) if str(e).strip()]
        evidence = [e for e in evidence if _valid_evidence(e, root)]
        if title and why and evidence:
            decisions.append({"title": title, "why": why, "evidence": evidence[:8]})
        elif title:
            warnings.append(f"{node['id']}: решение «{title}» отброшено — нет подтверждения в коде")
    links = []
    seen_links = set()
    for item in result.get("links") or []:
        if not isinstance(item, dict):
            continue
        to = str(item.get("to") or "").strip()
        kind_link = _normal_link_type(item.get("type"))
        if to in index and to != node["id"] and (to, kind_link) not in seen_links:
            seen_links.add((to, kind_link))
            links.append({"to": to, "type": kind_link})
    todo = [str(t).strip()[:300] for t in (result.get("todo") or []) if str(t).strip()][:10]

    for key, value in (("summary", summary), ("overview", overview), ("status", status),
                       ("confidence", round(confidence, 2)), ("kind", kind)):
        if node.get(key) != value:
            node[key] = value
            changed = True
    if node.get("decisions") != decisions[:8]:
        node["decisions"] = decisions[:8]
        changed = True
    if node.get("links") != links[:12]:
        node["links"] = links[:12]
        changed = True
    if node.get("todo") != todo:
        node["todo"] = todo
        changed = True
    return changed


def _add_locked_note(node: dict[str, Any], current_hash: str, warnings: list[str]) -> bool:
    note = f"Код изменился (hash {current_hash}) — узел заблокирован, проверьте вручную."
    todo = node.setdefault("todo", [])
    if note in todo:
        return False
    todo.append(note)
    warnings.append(f"{node['id']}: заблокирован, но код изменился — добавлена заметка в todo")
    return True


# ─── build ───────────────────────────────────────────────────────────────────


def _progress(progress: Optional[Callable[..., None]], **payload: Any) -> None:
    if progress:
        try:
            progress(**payload)
        except TypeError:
            progress(payload)  # type: ignore[misc]


def build(
    root: str | Path,
    *,
    llm: Any = None,
    scope: str = "stale",
    cfg: BuildConfig | None = None,
    deep: bool = False,
    progress: Optional[Callable[..., None]] = None,
    confirm: str | None = None,
) -> dict[str, Any]:
    base = Path(root).expanduser().resolve()
    cfg = cfg or BuildConfig()
    facts = model.read_json(model.facts_path(base)) or facts_mod.scan(base)
    warnings: list[str] = []
    notes: list[str] = []

    existing = model.load_tree_raw(base)
    draft = structure.propose(facts, base, max_branches=cfg.max_branches, max_depth=cfg.max_depth)

    adapter = StructuredLlm(llm) if llm is not None else None
    if adapter is not None and (existing is None or deep):
        try:
            result = adapter.structured(
                instructions=SYSTEM_STRUCTURE,
                input_text=structure_prompt(facts_mod.digest(facts), draft),
                schema=STRUCTURE_SCHEMA,
                schema_name="atlas.structure",
                max_tokens=1800,
                temperature=0.1,
            )
            if result and isinstance(result.get("branches"), list):
                draft = _tree_from_branches(result["branches"], base, cfg)
                notes.append(f"структура уточнена моделью: {len(draft['nodes'])} узлов")
            else:
                warnings.append("модель не вернула ветки — оставлена эвристика")
        except Exception as exc:  # noqa: BLE001 - the build must survive a provider error
            warnings.append(f"структурный проход не удался: {exc}")

    if existing is None:
        tree = draft
        changes = {"added": [n["id"] for n in tree["nodes"]], "kept": [], "missing": []}
    else:
        tree, changes = structure.merge(existing, draft, base)

    targets = select_targets(tree, scope, base, facts)
    if len(targets) > cfg.max_nodes_per_run:
        warnings.append(f"за один прогон обрабатывается не больше {cfg.max_nodes_per_run} узлов")
        targets = targets[: cfg.max_nodes_per_run]

    # the same plan the UI showed, recomputed on the server
    if scope in {"all", "undescribed"} and len(targets) > 1:
        planned = plan(base, tree, facts, scope, cfg)
        # A deep build refines the branch list *after* the plan was shown, so the
        # exact hash cannot match; the confirmation itself still has to be present.
        if deep:
            if not confirm:
                raise ValueError("build plan was not confirmed: pass the plan hash returned by /plan")
        elif confirm != planned["hash"]:
            raise ValueError("build plan was not confirmed: pass the plan hash returned by /plan")

    cache = model.cache_path(base)
    updated: list[str] = []
    skipped_locked: list[str] = []
    unchanged = 0
    total = len(targets)

    for i, node in enumerate(targets, 1):
        current = facts_mod.hash_paths(base, node.get("paths") or [], cache_file=cache)
        stale = current.get("hash") != node.get("sourceHash")
        _progress(progress, done=i - 1, total=total, node=node["id"], phase="read")

        if node.get("locked"):
            skipped_locked.append(node["id"])
            if stale and _add_locked_note(node, current.get("hash") or "?", warnings):
                pass
            unchanged += 1
            continue
        if scope == "stale" and not stale and node.get("status") != "none":
            unchanged += 1
            continue

        evidence = build_evidence(node, base, cfg)
        if not evidence.text.strip():
            node["status"] = "none"
            node["confidence"] = 0.1
            node["sourceHash"] = current.get("hash") or node.get("sourceHash") or "0" * 12
            note = "Не удалось прочитать код по путям узла."
            if note not in (node.get("todo") or []):
                node.setdefault("todo", []).append(note)
            updated.append(node["id"])
            _progress(progress, done=i, total=total, node=node["id"], phase="empty")
            continue

        if adapter is None:
            # deterministic draft only: never fake a description
            node["sourceHash"] = current.get("hash") or node.get("sourceHash")
            unchanged += 1
            continue

        _progress(progress, done=i - 1, total=total, node=node["id"], phase="describe")
        prompt_text = doc_prompt(node, facts_mod.digest(facts), evidence, tree, tree.get("orphans") or [])
        result = None
        for attempt in (1, 2):
            try:
                result = adapter.structured(
                    instructions=SYSTEM_DOC if attempt == 1 else SYSTEM_DOC + "\n\nВАЖНО: верни только JSON по схеме, без пояснений.",
                    input_text=prompt_text,
                    schema=DOC_SCHEMA,
                    schema_name="atlas.node",
                    max_tokens=1600,
                    temperature=0.2,
                )
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"{node['id']}: модель не ответила — {exc}")
                break
            if result:
                break
            warnings.append(f"{node['id']}: пустой ответ модели (попытка {attempt})")

        if not result:
            raw = (getattr(adapter, "last_text", "") or "").strip().replace("\n", " ")[:160]
            if raw:
                warnings.append(f"{node['id']}: сырой ответ — {raw}")
            if not node.get("summary"):
                names = ", ".join(Path(p).name for p in (evidence.paths or [])[:5])
                node["summary"] = f"Код найден ({evidence.files} файлов: {names}), но модель не вернула описание."
                node["status"] = "none"
                node["confidence"] = 0.15
            node["sourceHash"] = current.get("hash") or node.get("sourceHash") or "0" * 12
            node.setdefault("todo", []).append("Не удалось получить описание от модели; попробуйте обновить узел.")
            updated.append(node["id"])
            continue

        changed = _apply_doc(node, result, base, tree, warnings)
        node["sourceHash"] = current.get("hash") or node.get("sourceHash") or "0" * 12
        if changed:
            updated.append(node["id"])
        else:
            unchanged += 1
        _progress(progress, done=i, total=total, node=node["id"], phase="done")

    tree["orphans"] = structure.find_orphans(tree, facts, base)
    model.touch_generated(tree)
    errors, validation_warnings = model.validate(tree, base)
    warnings.extend(validation_warnings[:10])
    if errors:
        raise ValueError("atlas.yaml validation failed: " + "; ".join(errors[:5]))
    model.save_tree_raw(base, tree)

    lines = [
        f"scope: {scope} · узлов в карте: {len(tree.get('nodes') or [])}",
        f"обновлено: {', '.join(updated) if updated else '—'}",
        f"добавлено: {', '.join(changes['added']) if changes['added'] else '—'}",
        f"без изменений: {unchanged}",
        f"заблокировано, пропущено: {', '.join(skipped_locked) if skipped_locked else '—'}",
        f"сирот: {len(tree.get('orphans') or [])}",
    ]
    if notes:
        lines.append("заметки: " + "; ".join(notes))
    if warnings:
        lines.append("предупреждения: " + "; ".join(warnings[:8]))
    model.append_changelog(base, f"build · {scope}", lines)

    return {
        "ok": True,
        "project": tree.get("project"),
        "scope": scope,
        "updated": updated,
        "added": changes["added"],
        "unchanged": unchanged,
        "skipped_locked": skipped_locked,
        "orphans": len(tree.get("orphans") or []),
        "warnings": warnings,
        "coverage": model.coverage(tree.get("nodes") or []),
        "generated": tree.get("generated"),
    }


def _tree_from_branches(branches: list[Any], root: Path, cfg: BuildConfig) -> dict[str, Any]:
    used: set[str] = set()
    nodes: list[dict[str, Any]] = []

    def walk(items: list[Any], parent: str, depth: int) -> None:
        for item in items[: cfg.max_branches if depth == 1 else 4]:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()[:80]
            paths = []
            for rel in item.get("paths") or []:
                rel = str(rel).strip().lstrip("./").rstrip("/") or "."
                target = (root / rel).resolve()
                if (target == root or root in target.parents) and target.exists() and rel not in paths:
                    paths.append(rel)
            if not name or not paths:
                continue
            nid = model.unique_id(name, used)
            hashed = facts_mod.hash_paths(root, paths)
            nodes.append({
                "id": nid,
                "name": name,
                "parent": parent,
                "kind": item.get("kind") if item.get("kind") in model.KINDS else "other",
                "summary": str(item.get("summary") or "")[:240],
                "status": "none",
                "confidence": 0.25,
                "locked": False,
                "paths": paths[:6],
                "sourceHash": hashed.get("hash") or "0" * 12,
                "decisions": [],
                "links": [],
                "todo": [],
            })
            if depth < cfg.max_depth and isinstance(item.get("children"), list):
                walk(item["children"], nid, depth + 1)

    walk(branches, "root", 1)
    if not nodes:
        raise ValueError("model returned no branch with an existing path")
    tree = model.new_tree(root.name)
    tree["nodes"] = nodes
    return tree

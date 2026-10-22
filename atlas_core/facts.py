"""Deterministic project scanner — no model, no network, no writes outside .atlas.

The output is evidence, not prose: file inventory, manifests, entrypoints,
services, routes, docs, TODOs, commits.  The agent layer turns this into
``atlas.yaml``; the renderer never runs the scanner.

Safety rules enforced here, not by convention:

* dotenv files, private keys and credential-looking names are skipped by name;
* binary files are detected and never read as text;
* ``.git``, virtualenvs, ``node_modules``, build output and caches are pruned;
* a file larger than ``MAX_TEXT_BYTES`` is counted but not parsed;
* the only file written is ``<project>/.atlas/facts.json`` (plus the hash cache).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

try:
    from ruamel.yaml import YAML
except Exception:  # pragma: no cover
    YAML = None  # type: ignore[assignment]


def _safe_yaml():
    return YAML(typ="safe") if YAML is not None else None

from . import model

MAX_FILES = 60_000
MAX_TEXT_BYTES = 512 * 1024
MAX_SCAN_BYTES = 24 * 1024 * 1024
MAX_ROUTE_FILES = 800
MAX_HASH_FILES = 1500
MAX_HASH_BYTES = 64 * 1024 * 1024

PRUNE_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "bower_components", ".venv", "venv", "env",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".next", ".nuxt", ".svelte-kit",
    "dist", "build", "out", "target", "coverage", ".coverage", ".cache", ".parcel-cache",
    ".turbo", ".idea", ".vscode", ".DS_Store", "vendor", "Pods", ".terraform", "site-packages",
    "site", ".eggs", "*.egg-info", "playwright-report", "test-results", ".atlas",
}

SKIP_EXTS = {
    # binaries / media
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".ico", ".bmp", ".tiff", ".svgz",
    ".mp3", ".mp4", ".wav", ".ogg", ".webm", ".mov", ".avi", ".mkv", ".flac",
    ".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar", ".dmg", ".iso",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".pages", ".numbers",
    ".woff", ".woff2", ".ttf", ".otf", ".eot", ".wasm", ".class", ".jar", ".pyc", ".pyo",
    ".so", ".dylib", ".dll", ".exe", ".bin", ".db", ".sqlite", ".sqlite3", ".mdb", ".pdb",
    ".lockb",
}

SECRET_NAMES = (
    ".env", ".env.", "id_rsa", "id_ed25519", "id_ecdsa", ".npmrc", ".pypirc", ".netrc",
    "auth.json", "credentials", "credentials.json", "secrets", "secret.", ".htpasswd",
    "keystore", ".keystore", "*.pem", "*.key", "*.p12", "*.pfx", "*.jks", "*.asc",
    "service-account", "service_account", "firebase-adminsdk", ".git-credentials",
)

CODE_EXTS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".go", ".rb", ".php", ".java",
    ".kt", ".kts", ".rs", ".cs", ".swift", ".vue", ".svelte", ".astro", ".sql", ".sh",
    ".bash", ".zsh", ".fish", ".lua", ".ex", ".exs", ".clj", ".scala", ".dart",
}

MANIFEST_NAMES = {
    "package.json", "pyproject.toml", "setup.py", "setup.cfg", "cargo.toml", "go.mod",
    "composer.json", "gemfile", "pom.xml", "build.gradle", "build.gradle.kts",
    "dockerfile", "docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml",
    "procfile", "makefile", "pnpm-workspace.yaml", "turbo.json", "nx.json", "lerna.json",
    "requirements.txt", "uv.lock", "poetry.lock", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
}

LOCKFILES = {
    "package-lock.json": "npm", "yarn.lock": "yarn", "pnpm-lock.yaml": "pnpm",
    "bun.lockb": "bun", "uv.lock": "uv", "poetry.lock": "poetry", "Pipfile.lock": "pipenv",
}

ENTRY_NAMES = {
    "main.py", "app.py", "cli.py", "server.py", "manage.py", "__main__.py", "wsgi.py", "asgi.py",
    "main.go", "index.js", "index.ts", "index.tsx", "main.js", "main.ts", "main.tsx",
    "app.js", "app.ts", "app.tsx", "server.js", "server.ts", "bot.py", "worker.py",
}

ROUTE_PATTERNS = [
    (re.compile(r"@(?:app|router|bp|blueprint)\.(get|post|put|patch|delete)\(\s*[\"']([^\"']{1,200})[\"']", re.I), "fastapi/flask"),
    (re.compile(r"@(?:app|bp)\.route\(\s*[\"']([^\"']{1,200})[\"']"), "flask"),
    (re.compile(r"\b(?:app|router|server|api)\.(get|post|put|patch|delete)\(\s*[`'\"]([^`'\"]{1,200})[`'\"]"), "express/fastify/hono"),
    (re.compile(r"\b(?:r|router|mux|e)\.(GET|POST|PUT|PATCH|DELETE|HandleFunc)\(\s*[\"']([^\"']{1,200})[\"']"), "go"),
    (re.compile(r"\bpath\(\s*[\"']([^\"']{1,200})[\"']"), "django"),
    (re.compile(r"<Route[^>]{0,120}\bpath=[\"']([^\"']{1,200})[\"']"), "react-router"),
    (re.compile(r"\bpath:\s*[\"']([^\"']{1,200})[\"']"), "router-config"),
]

TODO_RE = re.compile(r"\b(TODO|FIXME|HACK|XXX)\b[:\s-]*(.{0,160})", re.I)
HEADING_RE = re.compile(r"^\s*#\s+(.+?)\s*$", re.M)


def _run(args: list[str], cwd: Path, timeout: float = 20.0) -> str | None:
    try:
        proc = subprocess.run(
            args, cwd=str(cwd), capture_output=True, text=True, timeout=timeout,
            errors="replace", check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def _is_secret(rel: str) -> bool:
    name = Path(rel).name.lower()
    if any(part in name for part in ("secret", "credential", "service-account", "service_account")):
        return True
    for pattern in SECRET_NAMES:
        if pattern.startswith("*"):
            if name.endswith(pattern[1:]):
                return True
        elif pattern.endswith("."):
            if name.startswith(pattern):
                return True
        elif name == pattern or name.startswith(pattern):
            return True
    return False


def _is_code(rel: str) -> bool:
    return Path(rel).suffix.lower() in CODE_EXTS


def _is_binary(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            chunk = fh.read(8192)
    except OSError:
        return True
    if not chunk:
        return False
    if b"\x00" in chunk:
        return True
    sample = chunk[:4096]
    printable = sum(1 for b in sample if 9 <= b <= 13 or 32 <= b <= 126 or b >= 128)
    return printable / max(1, len(sample)) < 0.85


def _read_text(path: Path, limit: int = MAX_TEXT_BYTES) -> str | None:
    try:
        with path.open("rb") as fh:
            raw = fh.read(limit + 1)
    except OSError:
        return None
    if len(raw) > limit or b"\x00" in raw[:8192]:
        return None
    return raw.decode("utf-8", errors="replace")


def _git_files(root: Path) -> list[str] | None:
    out = _run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], root)
    if out is None:
        return None
    files = [p for p in out.split("\x00") if p]
    return files


def _walk_files(root: Path) -> list[str]:
    files: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in PRUNE_DIRS and not d.endswith(".egg-info") and not d.startswith(".atlas")
        )
        rel_dir = Path(dirpath).relative_to(root)
        for name in sorted(filenames):
            if len(files) >= MAX_FILES:
                return files
            rel = (rel_dir / name).as_posix() if str(rel_dir) != "." else name
            if _is_secret(rel):
                continue
            if Path(name).suffix.lower() in SKIP_EXTS and name.lower() not in MANIFEST_NAMES:
                continue
            files.append(rel)
    return files


def file_index(root: Path) -> list[str]:
    files = _git_files(root)
    if files is None:
        files = _walk_files(root)
    cleaned = []
    for rel in files:
        pure = Path(rel)
        if any(part in PRUNE_DIRS or part.startswith(".atlas") for part in pure.parts):
            continue
        if _is_secret(rel):
            continue
        if pure.suffix.lower() in SKIP_EXTS and pure.name.lower() not in MANIFEST_NAMES:
            continue
        cleaned.append(pure.as_posix())
    return sorted(set(cleaned))[:MAX_FILES]


# ─── manifests ───────────────────────────────────────────────────────────────


def _load_yaml_safe(path: Path):
    yaml = _safe_yaml()
    if yaml is None:
        return None
    try:
        with path.open("r", encoding="utf-8") as fh:
            return yaml.load(fh)
    except Exception:
        return None


def _parse_package_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    workspaces = data.get("workspaces")
    if isinstance(workspaces, dict):
        workspaces = workspaces.get("packages") or []
    if not isinstance(workspaces, list):
        workspaces = []
    scripts = data.get("scripts") if isinstance(data.get("scripts"), dict) else {}
    deps = {}
    for key in ("dependencies", "devDependencies", "peerDependencies"):
        if isinstance(data.get(key), dict):
            deps.update(data[key])
    entry = []
    for key in ("main", "module", "types", "browser"):
        value = data.get(key)
        if isinstance(value, str):
            entry.append(value)
    bin_value = data.get("bin")
    if isinstance(bin_value, str):
        entry.append(bin_value)
    elif isinstance(bin_value, dict):
        entry.extend(v for v in bin_value.values() if isinstance(v, str))
    return {
        "path": path.as_posix(),
        "kind": "package.json",
        "name": data.get("name") or path.parent.name,
        "version": data.get("version"),
        "private": bool(data.get("private")),
        "package_manager": data.get("packageManager"),
        "workspaces": [w for w in workspaces if isinstance(w, str)][:40],
        "scripts": {str(k): str(v)[:200] for k, v in list(scripts.items())[:30]},
        "deps": sorted(deps)[:200],
        "deps_count": len(deps),
        "entrypoints": [e for e in entry if isinstance(e, str)][:10],
        "description": data.get("description"),
    }


def _parse_pyproject(path: Path) -> dict[str, Any] | None:
    try:
        import tomllib
    except ImportError:  # pragma: no cover
        return None
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, ValueError):
        return None
    project = data.get("project") if isinstance(data.get("project"), dict) else {}
    poetry = ((data.get("tool") or {}).get("poetry") or {}) if isinstance(data.get("tool"), dict) else {}
    scripts = project.get("scripts") or poetry.get("scripts") or {}
    if not isinstance(scripts, dict):
        scripts = {}
    deps = project.get("dependencies") or poetry.get("dependencies") or []
    if isinstance(deps, dict):
        deps = list(deps)
    if not isinstance(deps, list):
        deps = []
    members = []
    uv = ((data.get("tool") or {}).get("uv") or {}) if isinstance(data.get("tool"), dict) else {}
    if isinstance(uv.get("workspace"), dict):
        members = uv["workspace"].get("members") or []
    return {
        "path": path.as_posix(),
        "kind": "pyproject.toml",
        "name": project.get("name") or poetry.get("name") or path.parent.name,
        "version": str(project.get("version") or poetry.get("version") or ""),
        "workspaces": [m for m in members if isinstance(m, str)][:40],
        "scripts": {str(k): str(v)[:200] for k, v in scripts.items()},
        "deps": [str(d)[:80] for d in deps][:200],
        "deps_count": len(deps),
        "entrypoints": [],
    }


def _parse_cargo(path: Path) -> dict[str, Any] | None:
    try:
        import tomllib
    except ImportError:  # pragma: no cover
        return None
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, ValueError):
        return None
    package = data.get("package") if isinstance(data.get("package"), dict) else {}
    workspace = data.get("workspace") if isinstance(data.get("workspace"), dict) else {}
    bins = data.get("bin") if isinstance(data.get("bin"), list) else []
    return {
        "path": path.as_posix(),
        "kind": "Cargo.toml",
        "name": package.get("name") or path.parent.name,
        "version": str(package.get("version") or ""),
        "workspaces": [m for m in workspace.get("members") or [] if isinstance(m, str)][:40],
        "scripts": {},
        "deps": [],
        "deps_count": 0,
        "entrypoints": [b.get("path") for b in bins if isinstance(b, dict) and b.get("path")][:10],
    }


def _parse_compose(path: Path) -> dict[str, Any] | None:
    data = _load_yaml_safe(path)
    if not isinstance(data, dict) or not isinstance(data.get("services"), dict):
        return None
    services = []
    for name, cfg in list(data["services"].items())[:60]:
        cfg = cfg if isinstance(cfg, dict) else {}
        build = cfg.get("build")
        context = "."
        if isinstance(build, str):
            context = build
        elif isinstance(build, dict) and isinstance(build.get("context"), str):
            context = build["context"]
        depends = cfg.get("depends_on") or []
        if isinstance(depends, dict):
            depends = list(depends)
        services.append({
            "name": str(name),
            "path": context,
            "image": cfg.get("image") if isinstance(cfg.get("image"), str) else None,
            "ports": [str(p) for p in (cfg.get("ports") or [])][:8] if isinstance(cfg.get("ports"), list) else [],
            "depends_on": [str(d) for d in depends][:10] if isinstance(depends, list) else [],
            "command": str(cfg.get("command"))[:120] if cfg.get("command") else None,
            "profiles": [str(p) for p in (cfg.get("profiles") or [])][:6] if isinstance(cfg.get("profiles"), list) else [],
        })
    return {"path": path.as_posix(), "kind": "compose", "name": path.parent.name, "services": services}


def _parse_dockerfile(path: Path) -> dict[str, Any]:
    text = _read_text(path, 256 * 1024) or ""
    base = re.search(r"^\s*FROM\s+(\S+)", text, re.M | re.I)
    cmd = re.search(r"^\s*(?:CMD|ENTRYPOINT)\s+(.+)$", text, re.M | re.I)
    expose = re.findall(r"^\s*EXPOSE\s+(\d+)", text, re.M | re.I)
    return {
        "path": path.as_posix(), "kind": "dockerfile", "name": path.parent.name,
        "base": base.group(1) if base else None,
        "command": cmd.group(1)[:200] if cmd else None,
        "ports": expose[:8],
    }


def _parse_go_mod(path: Path) -> dict[str, Any] | None:
    text = _read_text(path, 256 * 1024)
    if not text:
        return None
    module = re.search(r"^module\s+(\S+)", text, re.M)
    go = re.search(r"^go\s+(\S+)", text, re.M)
    return {
        "path": path.as_posix(), "kind": "go.mod", "name": (module.group(1).split("/")[-1] if module else path.parent.name),
        "module": module.group(1) if module else None, "go": go.group(1) if go else None,
        "workspaces": [], "scripts": {}, "deps": [], "deps_count": 0, "entrypoints": [],
    }


def _parse_k8s(path: Path) -> list[dict[str, Any]]:
    yaml = _safe_yaml()
    if yaml is None:
        return []
    try:
        with path.open("r", encoding="utf-8") as fh:
            docs = list(yaml.load_all(fh))
    except Exception:
        return []
    out = []
    for doc in docs:
        if isinstance(doc, dict) and doc.get("kind") in {"Deployment", "StatefulSet", "DaemonSet", "CronJob", "Job"}:
            meta = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
            out.append({"name": str(meta.get("name") or path.stem), "kind": str(doc.get("kind")), "file": path.as_posix()})
    return out


def manifests(root: Path, files: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    found: list[dict[str, Any]] = []
    services: list[dict[str, Any]] = []
    k8s: list[dict[str, Any]] = []
    for rel in files:
        name = Path(rel).name
        lower = name.lower()
        if lower not in MANIFEST_NAMES:
            continue
        path = root / rel
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > 2 * 1024 * 1024:
            continue
        info: dict[str, Any] | None = None
        if lower == "package.json":
            info = _parse_package_json(path)
        elif lower == "pyproject.toml":
            info = _parse_pyproject(path)
        elif lower == "cargo.toml":
            info = _parse_cargo(path)
        elif lower in {"docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"}:
            info = _parse_compose(path)
            if info:
                services.extend(info.get("services") or [])
        elif lower == "dockerfile":
            info = _parse_dockerfile(path)
        elif lower == "go.mod":
            info = _parse_go_mod(path)
        elif lower == "procfile":
            text = _read_text(path, 64 * 1024) or ""
            for line in text.splitlines():
                if ":" in line and not line.strip().startswith("#"):
                    proc, _, cmd = line.partition(":")
                    services.append({"name": proc.strip(), "path": ".", "command": cmd.strip()[:160], "source": rel})
            info = {"path": rel, "kind": "procfile", "name": path.parent.name}
        elif lower in {"pnpm-workspace.yaml", "turbo.json", "nx.json", "lerna.json"}:
            info = {"path": rel, "kind": lower, "name": path.parent.name}
        else:
            info = {"path": rel, "kind": lower, "name": path.parent.name}
        if info:
            info["path"] = rel  # parsers see an absolute path; the tree stores project-relative
            found.append(info)
        if lower.endswith((".yaml", ".yml")) and re.search(r"(k8s|kube|deploy|manifests|helm)", rel, re.I) and size < 512 * 1024:
            k8s.extend(_parse_k8s(path))
    return found, services, k8s


# ─── entrypoints, routes, docs, todos ────────────────────────────────────────


def entrypoints(root: Path, files: list[str], mans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(path: str, kind: str, evidence: str) -> None:
        if path in seen or len(out) >= 80:
            return
        seen.add(path)
        out.append({"path": path, "kind": kind, "evidence": evidence})

    for man in mans:
        for rel in man.get("entrypoints") or []:
            rel = str(rel).lstrip("./")
            if rel and (root / rel).exists():
                add(rel, "declared", f"{man.get('path')} entrypoint")

    for rel in files:
        name = Path(rel).name
        if name in ENTRY_NAMES and rel.count("/") <= 4:
            add(rel, "file", "conventional entry filename")
        elif rel.startswith("bin/") and rel.count("/") == 1:
            text = _read_text(root / rel, 4096) or ""
            if text.startswith("#!"):
                add(rel, "bin", text.splitlines()[0][:80])
    return out


def _next_route(rel: str) -> str | None:
    """Map a Next.js file to its URL.  App router and pages router differ."""
    parts = Path(rel).parts
    lower = [p.lower() for p in parts]
    if "app" in lower:
        i = lower.index("app")
        rest = list(parts[i + 1:])
        if not rest or rest[-1].split(".")[0] not in {"page", "route"}:
            return None
        rest = rest[:-1]
    elif "pages" in lower:
        i = lower.index("pages")
        rest = list(parts[i + 1:])
        if not rest:
            return None
        stem = rest[-1].split(".")[0]
        if stem in {"_app", "_document", "_error", "404", "500"}:
            return None
        rest = rest[:-1] + ([] if stem == "index" else [stem])
    else:
        return None
    cleaned = []
    for segment in rest:
        segment = re.sub(r"^\[(.+)\]$", r":\1", segment.strip())
        if segment:
            cleaned.append(segment)
    return "/" + "/".join(cleaned)


def routes(root: Path, files: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    next_app = any(Path(f).name.startswith("next.config.") for f in files) or any(
        f.startswith("app/") or f.startswith("src/app/") for f in files
    )
    candidates = [f for f in files if _is_code(f) and not any(
        part in f for part in ("/test", "test_", ".test.", ".spec.", "__tests__", "/migrations/")
    )]
    for rel in candidates[:MAX_ROUTE_FILES]:
        path = root / rel
        try:
            if path.stat().st_size > 256 * 1024:
                continue
        except OSError:
            continue
        text = _read_text(path, 256 * 1024)
        if not text:
            continue
        lower = rel.lower()
        if next_app:
            route = _next_route(rel)
            if route is not None:
                out.append({"file": rel, "method": "GET", "path": route, "framework": "next"})
                continue
        for pattern, framework in ROUTE_PATTERNS:
            for match in pattern.finditer(text):
                if framework == "flask" or framework == "django" or framework == "react-router" and len(match.groups()) == 1:
                    method, route = "GET", match.group(1)
                elif framework == "router-config":
                    continue
                else:
                    method, route = match.group(1).upper(), match.group(2)
                if not route.startswith("/"):
                    continue
                out.append({"file": rel, "method": method, "path": route[:200], "framework": framework})
                if len(out) >= 400:
                    return out
    return out


def docs(root: Path, files: list[str]) -> list[dict[str, Any]]:
    out = []
    for rel in files:
        lower = rel.lower()
        if not lower.endswith((".md", ".mdx", ".rst")):
            continue
        if not (Path(rel).name.lower().startswith("readme") or "/docs/" in f"/{lower}" or lower.startswith("docs/")
                or "/adr/" in f"/{lower}" or "/decisions/" in f"/{lower}" or rel.count("/") == 0):
            continue
        path = root / rel
        try:
            size = path.stat().st_size
        except OSError:
            continue
        text = _read_text(path, 200 * 1024) or ""
        title = Path(rel).stem
        match = HEADING_RE.search(text)
        if match:
            title = match.group(1).strip()[:120]
        out.append({"path": rel, "title": title, "bytes": size})
        if len(out) >= 120:
            break
    return out


def todos(root: Path, files: list[str]) -> list[dict[str, Any]]:
    out = []
    for rel in files:
        if not _is_code(rel):
            continue
        text = _read_text(root / rel, 256 * 1024)
        if not text:
            continue
        for match in TODO_RE.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            out.append({"path": rel, "line": line, "kind": match.group(1).upper(), "text": match.group(2).strip()[:160]})
            if len(out) >= 200:
                return out
    return out


# ─── inventory ────────────────────────────────────────────────────────────────


def inventory(root: Path, files: list[str]) -> dict[str, Any]:
    by_ext: Counter[str] = Counter()
    total_bytes = 0
    dir_files: Counter[str] = Counter()
    direct: Counter[str] = Counter()
    for rel in files:
        ext = Path(rel).suffix.lower() or "(none)"
        by_ext[ext] += 1
        try:
            total_bytes += (root / rel).stat().st_size
        except OSError:
            pass
        parent = str(Path(rel).parent.as_posix())
        direct[parent] += 1
        parts = Path(rel).parts[:-1]
        for i in range(1, min(len(parts), 4) + 1):
            dir_files["/".join(parts[:i])] += 1

    dirs = []
    for path, count in sorted(dir_files.items(), key=lambda kv: (-kv[1], kv[0]))[:800]:
        dirs.append({"path": path, "files": count, "direct": direct.get(path, 0)})

    entries = []
    for entry in sorted(root.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        if entry.name in PRUNE_DIRS or entry.name.startswith(".atlas"):
            continue
        if entry.name in {".git", ".DS_Store"}:
            continue
        if entry.is_file() and _is_secret(entry.name):
            continue
        rel = entry.name
        is_dir = entry.is_dir()
        count = dir_files.get(rel, 0) if is_dir else 1
        if is_dir and count == 0:
            continue
        is_repo = is_dir and (entry / ".git").exists()
        manifest = None
        if is_dir:
            for name in ("package.json", "pyproject.toml", "Cargo.toml", "go.mod", "docker-compose.yml", "compose.yaml", "Dockerfile"):
                if (entry / name).exists():
                    manifest = name
                    break
        entries.append({
            "path": rel, "name": entry.name, "dir": is_dir, "files": count,
            "repo": is_repo, "manifest": manifest,
        })
    return {
        "total": len(files),
        "bytes": total_bytes,
        "by_ext": [{"ext": ext, "files": n} for ext, n in by_ext.most_common(30)],
        "dirs": dirs,
        "top": entries[:80],
    }


def vcs(root: Path) -> dict[str, Any]:
    if not (root / ".git").exists():
        return {"type": None}
    branch = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], root)
    head = _run(["git", "rev-parse", "--short", "HEAD"], root)
    log = _run(["git", "log", "-n", "60", "--pretty=format:%h%x09%ad%x09%s", "--date=short"], root)
    commits = []
    for line in (log or "").splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3:
            commits.append({"sha": parts[0], "date": parts[1], "subject": parts[2][:160]})
    status = _run(["git", "status", "--porcelain"], root) or ""
    return {
        "type": "git",
        "branch": (branch or "").strip() or None,
        "head": (head or "").strip() or None,
        "dirty": bool(status.strip()),
        "commits": commits,
    }


def scan(root: str | Path, *, max_files: int = MAX_FILES) -> dict[str, Any]:
    base = Path(root).expanduser().resolve()
    if not base.is_dir():
        raise ValueError(f"not a directory: {base}")
    started = time.time()
    files = file_index(base)[:max_files]
    mans, services, k8s = manifests(base, files)
    facts = {
        "schema": 1,
        "scanned_at": model.utcnow(),
        "root": str(base),
        "name": base.name,
        "vcs": vcs(base),
        "inventory": inventory(base, files),
        "manifests": mans[:120],
        "services": (services + [{"name": s["name"], "path": ".", "kind": s["kind"], "source": s["file"]} for s in k8s])[:80],
        "entrypoints": entrypoints(base, files, mans),
        "routes": routes(base, files),
        "docs": docs(base, files),
        "todos": todos(base, files),
        "warnings": [],
        "scan_ms": int((time.time() - started) * 1000),
    }
    if len(files) >= max_files:
        facts["warnings"].append(f"inventory capped at {max_files} files")
    out = model.facts_path(base)
    model.write_json(out, facts)
    return facts


# ─── hashing ──────────────────────────────────────────────────────────────────


def _iter_under(base: Path, rel: str):
    target = (base / rel).resolve()
    if base not in target.parents and target != base:
        return
    if target.is_file():
        if not _is_secret(rel):
            yield target
        return
    if not target.is_dir():
        return
    for dirpath, dirnames, filenames in os.walk(target):
        dirnames[:] = sorted(d for d in dirnames if d not in PRUNE_DIRS and not d.startswith(".atlas"))
        for name in sorted(filenames):
            path = Path(dirpath) / name
            if _is_secret(path.as_posix()):
                continue
            if path.suffix.lower() in SKIP_EXTS:
                continue
            yield path


def hash_paths(
    root: str | Path, paths: Iterable[str], *, cache_file: Path | None = None,
    max_files: int = MAX_HASH_FILES, max_bytes: int = MAX_HASH_BYTES,
) -> dict[str, Any]:
    base = Path(root).expanduser().resolve()
    cache = model.read_json(cache_file, {}) if cache_file else {}
    if not isinstance(cache, dict):
        cache = {}
    new_cache: dict[str, Any] = {}
    rows: list[str] = []
    files = bytes_read = 0
    missing: list[str] = []
    truncated = False

    unique = []
    for rel in paths:
        rel = str(rel or "").strip()
        if rel and rel not in unique:
            unique.append(rel)

    for rel in unique:
        target = (base / rel)
        try:
            exists = target.exists()
        except OSError:
            exists = False
        if not exists:
            missing.append(rel)
            continue
        for path in _iter_under(base, rel):
            if files >= max_files or bytes_read >= max_bytes:
                truncated = True
                break
            try:
                stat = path.stat()
            except OSError:
                continue
            rel_file = path.relative_to(base).as_posix()
            digest = None
            cached = cache.get(rel_file)
            if isinstance(cached, dict) and cached.get("size") == stat.st_size and cached.get("mtime") == stat.st_mtime_ns:
                digest = cached.get("sha")
            if not isinstance(digest, str) or len(digest) != 64:
                h = hashlib.sha256()
                try:
                    with path.open("rb") as fh:
                        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                            h.update(chunk)
                    digest = h.hexdigest()
                except OSError:
                    continue
            new_cache[rel_file] = {"size": stat.st_size, "mtime": stat.st_mtime_ns, "sha": digest}
            files += 1
            bytes_read += stat.st_size
            rows.append(f"{rel_file}:{digest}")
        if truncated:
            break

    value = hashlib.sha256("\n".join(sorted(rows)).encode("utf-8")).hexdigest()[:12]
    if cache_file:
        model.write_json(cache_file, new_cache)
    return {"hash": value, "files": files, "bytes": bytes_read, "missing": missing, "truncated": truncated}


# ─── digest for prompts ────────────────────────────────────────────────────────


def node_history(root: str | Path, paths: Iterable[str], limit: int = 12) -> list[dict[str, Any]]:
    """Commits that touched the node's paths — the best evidence for decisions."""
    base = Path(root).expanduser().resolve()
    if not (base / ".git").exists():
        return []
    args = ["git", "log", f"-n{limit}", "--pretty=format:%h%x09%ad%x09%s", "--date=short", "--"]
    args.extend(str(p) for p in paths if p)
    out = _run(args, base)
    commits = []
    for line in (out or "").splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3:
            commits.append({"sha": parts[0], "date": parts[1], "subject": parts[2][:160]})
    return commits


def digest(facts: dict[str, Any], limit: int = 7000) -> str:
    inv = facts.get("inventory") or {}
    vcs = facts.get("vcs") or {}
    lines = [
        f"PROJECT: {facts.get('name')} ({facts.get('root')})",
        f"FILES: {inv.get('total', 0)} · {inv.get('bytes', 0)} bytes",
        "EXT: " + ", ".join(f"{e['ext']}×{e['files']}" for e in (inv.get("by_ext") or [])[:12]),
        "TOP: " + ", ".join(f"{e['path']}/ ({e['files']})" for e in (inv.get("top") or []) if e.get("dir")),
    ]
    if vcs.get("type"):
        lines.append(f"VCS: git {vcs.get('branch') or '?'} @ {vcs.get('head') or '?'} · {len(vcs.get('commits') or [])} commits")
    for man in (facts.get("manifests") or [])[:14]:
        extra = []
        if man.get("workspaces"):
            extra.append("workspaces=" + ",".join(man["workspaces"][:10]))
        if man.get("scripts"):
            extra.append("scripts=" + ",".join(list(man["scripts"])[:10]))
        if man.get("deps_count"):
            extra.append(f"deps={man['deps_count']}")
        lines.append(f"MANIFEST {man.get('path')} [{man.get('kind')}] name={man.get('name')} " + " ".join(extra))
    for ep in (facts.get("entrypoints") or [])[:20]:
        lines.append(f"ENTRY {ep['path']} ({ep['kind']}: {ep['evidence']})")
    for svc in (facts.get("services") or [])[:20]:
        lines.append(f"SERVICE {svc.get('name')} path={svc.get('path')} image={svc.get('image') or '-'}")
    routes = facts.get("routes") or []
    if routes:
        sample = ", ".join(f"{r['method']} {r['path']} ({r['file']})" for r in routes[:24])
        lines.append(f"ROUTES {len(routes)}: {sample}")
    for doc in (facts.get("docs") or [])[:16]:
        lines.append(f"DOC {doc['path']}: {doc['title']}")
    for commit in (vcs.get("commits") or [])[:20]:
        lines.append(f"COMMIT {commit['sha']} {commit['date']} {commit['subject']}")
    for todo in (facts.get("todos") or [])[:12]:
        lines.append(f"TODO {todo['path']}:{todo['line']} {todo['text']}")
    text = "\n".join(lines)
    if len(text) > limit:
        text = text[:limit] + f"\n… (digest truncated at {limit} chars)"
    return text


def candidates(root: str | Path, depth: int = 2) -> list[dict[str, Any]]:
    """Repo-ish folders under ``root`` for the UI's project picker."""
    base = Path(root).expanduser().resolve()
    out: list[dict[str, Any]] = []

    def is_repo(path: Path) -> bool:
        return (path / ".git").exists() or any(
            (path / name).exists()
            for name in ("package.json", "pyproject.toml", "Cargo.toml", "go.mod", "composer.json", "Gemfile")
        )

    if is_repo(base):
        out.append({"path": str(base), "name": base.name, "kind": "repo"})
    for dirpath, dirnames, _files in os.walk(base):
        current = Path(dirpath)
        rel_depth = len(current.relative_to(base).parts)
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in PRUNE_DIRS and not d.startswith(".") and d not in {"node_modules", "venv", "dist", "build"}
        )
        if rel_depth >= depth:
            dirnames[:] = []
        for name in dirnames:
            child = current / name
            if is_repo(child):
                out.append({"path": str(child), "name": child.name, "kind": "repo"})
        if len(out) >= 80:
            break
    seen = set()
    unique = []
    for item in out:
        if item["path"] not in seen:
            seen.add(item["path"])
            unique.append(item)
    return unique[:60]

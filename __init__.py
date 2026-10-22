"""Project Atlas — agent half.

Registers four tools, the ``/atlas`` slash command, ``hermes atlas <cmd>`` and a
bundled skill. The desktop half lives in ``desktop/plugin.js``; its backend is
``dashboard/plugin_api.py``. All three call the same ``atlas_core`` code.
"""

from __future__ import annotations

import argparse
import logging
import os
import shlex
from pathlib import Path

from . import schemas, tools
from .atlas_core import build as build_mod
from .atlas_core import cli as core_cli
from .atlas_core import model, render

logger = logging.getLogger(__name__)


def _config(ctx) -> build_mod.BuildConfig:
    return build_mod.BuildConfig.from_dict({
        "max_files_per_node": ctx.get_config("max_files_per_node", default=10),
        "max_node_chars": ctx.get_config("max_node_chars", default=24000),
        "max_branches": ctx.get_config("max_branches", default=8),
        "max_nodes_per_run": ctx.get_config("max_nodes_per_run", default=40),
    })


def register(ctx) -> None:
    cfg = _config(ctx)
    tools.configure(ctx, cfg)

    for schema in schemas.TOOLS:
        ctx.register_tool(
            name=schema["name"],
            toolset="atlas",
            schema=schema,
            handler=tools.HANDLERS[schema["name"]],
            description=schema["description"],
        )

    ctx.register_command(
        "atlas",
        _make_slash(ctx, cfg),
        description="Project Atlas: scan a project, build its map, show coverage or one node",
        args_hint="<scan|plan|build|status|node <id>> [path] [--scope stale|all|undescribed|node:<id>] [--yes <plan-hash>]",
    )

    ctx.register_cli_command(
        name="atlas",
        help="Project Atlas — scan a project, build/update atlas.yaml, inspect the tree",
        setup_fn=_setup_cli,
        handler_fn=_make_cli(ctx, cfg),
    )

    skills_dir = Path(__file__).resolve().parent / "skills"
    if skills_dir.is_dir():
        for child in sorted(skills_dir.iterdir()):
            skill_file = child / "SKILL.md"
            if skill_file.exists():
                ctx.register_skill(child.name, skill_file, description="Project Atlas: build and maintain a project map")


# ─── /atlas ───────────────────────────────────────────────────────────────────


def _make_slash(ctx, cfg):
    def handler(raw_args: str, **kwargs) -> str:
        try:
            parts = shlex.split(raw_args or "")
        except ValueError as exc:
            return f"Не разобрал аргументы: {exc}"
        if not parts or parts[0] in {"help", "-h", "--help"}:
            return (
                "/atlas scan [path] — просканировать проект\n"
                "/atlas plan [path] [--scope …] — показать план и стоимость\n"
                "/atlas build [path] [--scope …] [--yes <hash>] [--deep] — построить/обновить карту\n"
                "/atlas status [path] — покрытие, устаревшие узлы, сироты\n"
                "/atlas node <id> [path] — карточка узла\n"
                "Без пути берётся текущая папка сессии."
            )
        command = parts[0]
        args = parts[1:]
        if command == "node":
            if not args:
                return "Укажи id узла: /atlas node <id> [path]"
            node_id = args[0]
            project, _rest = _split_project(args[1:], kwargs)
            if not project:
                return "Не понял, какой проект. Укажи путь: /atlas node <id> /abs/path"
            return _node_text(tools.atlas_status({"project_path": project, "node_id": node_id}))
        project, rest = _split_project(args, kwargs)
        if not project:
            return "Не понял, какой проект. Укажи путь: /atlas scan /abs/path"
        scope = _flag(rest, "--scope", "stale")
        confirm = _flag(rest, "--yes", None)
        deep = "--deep" in rest

        if command == "scan":
            return _text(tools.atlas_scan({"project_path": project}))
        if command == "status":
            return _status_text(project)
        if command == "plan":
            return _plan_text(tools.atlas_plan({"project_path": project, "scope": scope}))
        if command == "build":
            result = tools.atlas_build({"project_path": project, "scope": scope, "confirm": confirm, "deep": deep})
            return _build_text(result)
        return f"Неизвестная команда {command!r}. /atlas help"
    return handler


def _split_project(args: list[str], kwargs: dict) -> tuple[str, list[str]]:
    # flags can carry values: drop those values from the positional list
    cleaned: list[str] = []
    skip = False
    for i, token in enumerate(args):
        if skip:
            skip = False
            continue
        if token in {"--scope", "--yes"}:
            skip = True
            continue
        if token.startswith("--"):
            continue
        cleaned.append(token)
    if cleaned:
        return os.path.abspath(os.path.expanduser(cleaned[0])), args[1:]
    cwd = str(kwargs.get("cwd") or "").strip()
    if cwd:
        return os.path.abspath(cwd), args
    return os.path.abspath(os.getcwd()), args


def _flag(args: list[str], name: str, default: str | None) -> str | None:
    for i, token in enumerate(args):
        if token == name and i + 1 < len(args):
            return args[i + 1]
        if token.startswith(name + "="):
            return token.split("=", 1)[1]
    return default


def _text(payload_json: str) -> str:
    import json
    try:
        data = json.loads(payload_json)
    except ValueError:
        return payload_json
    if not data.get("ok", True):
        return f"Ошибка: {data.get('error') or data}"
    return f"Готово: {data.get('root') or data.get('project') or ''} · файлов {data.get('files', '—')} · манифестов {data.get('manifests', '—') if isinstance(data.get('manifests'), int) else len(data.get('manifests') or [])}"


def _status_text(project: str) -> str:
    import json
    data = json.loads(tools.atlas_status({"project_path": project}))
    if not data.get("ok"):
        return f"Ошибка: {data.get('error')}"
    if not data.get("exists"):
        return f"Карты ещё нет. {data.get('hint')}"
    cov = data.get("coverage") or {}
    lines = [
        f"{data.get('project')} · полностью {cov.get('full', 0)} · частично {cov.get('partial', 0)} · без описания {cov.get('none', 0)}",
        data.get("tree_text") or "",
    ]
    if data.get("stale"):
        lines.append("Устарели: " + ", ".join(data["stale"]))
    if data.get("orphans"):
        lines.append("Сироты: " + ", ".join(o.get("path", "") for o in data["orphans"][:10]))
    return "\n".join(lines)


def _plan_text(payload_json: str) -> str:
    import json
    data = json.loads(payload_json)
    if not data.get("ok"):
        return f"Ошибка: {data.get('error')}"
    nodes = data.get("nodes") or []
    return (
        f"План: {len(nodes)} узлов · {data.get('files')} файлов · {data.get('bytes')} байт "
        f"≈ {data.get('approx_tokens')} токенов · {data.get('calls')} вызовов модели\n"
        f"узлы: {', '.join(n.get('id', '') for n in nodes)}\n"
        f"hash: {data.get('hash')}\n"
        "Если ок — /atlas build --scope " + str(data.get("scope")) + " --yes " + str(data.get("hash"))
    )


def _build_text(payload_json: str) -> str:
    import json
    data = json.loads(payload_json)
    if data.get("needs_confirmation"):
        plan = data.get("plan") or {}
        return _plan_text(json.dumps({"ok": True, **plan}))
    if not data.get("ok"):
        return f"Ошибка: {data.get('error')}"
    cov = data.get("coverage") or {}
    return (
        f"Карта обновлена: {data.get('project')} · узлов {cov.get('total')} · "
        f"полностью {cov.get('full')} · частично {cov.get('partial')} · без описания {cov.get('none')}\n"
        f"обновлено: {', '.join(data.get('updated') or []) or '—'}\n"
        + (data.get("tree_text") or "")
    )


def _node_text(payload_json: str) -> str:
    import json
    data = json.loads(payload_json)
    if not data.get("ok"):
        return f"Ошибка: {data.get('error')}"
    node = data.get("node") or {}
    lines = [f"{node.get('name')} [{node.get('id')}] — {model.STATUS_LABEL.get(node.get('status'), node.get('status'))}"]
    if node.get("summary"):
        lines.append(node["summary"])
    lines.append("пути: " + ", ".join(node.get("paths") or []))
    for decision in node.get("decisions") or []:
        lines.append(f"решение: {decision.get('title')} — {decision.get('why')}")
    if node.get("todo"):
        lines.append("вопросы: " + "; ".join(node["todo"]))
    return "\n".join(lines)


# ─── hermes atlas <cmd> ───────────────────────────────────────────────────────


def _setup_cli(subparser: argparse.ArgumentParser) -> None:
    sub = subparser.add_subparsers(dest="atlas_command")
    for name in ("scan", "draft", "plan", "build", "validate", "tree", "doctor"):
        parser = sub.add_parser(name)
        parser.add_argument("--project", "-p", default=".")
        parser.add_argument("--scope", default="stale")
        parser.add_argument("--confirm", default=None)
        parser.add_argument("--deep", action="store_true")
        parser.add_argument("--json", action="store_true")


def _make_cli(ctx, cfg):
    def handler(args) -> int:
        command = getattr(args, "atlas_command", None)
        if not command:
            print("usage: hermes atlas {scan|draft|plan|build|validate|tree|doctor} --project PATH")
            return 2
        argv = [command, "--project", getattr(args, "project", ".")]
        if getattr(args, "scope", None):
            argv += ["--scope", args.scope]
        if getattr(args, "confirm", None):
            argv += ["--confirm", args.confirm]
        if getattr(args, "deep", False):
            argv.append("--deep")
        if getattr(args, "json", False):
            argv.append("--json")
        return core_cli.main(argv, llm=ctx.llm, cfg=cfg)
    return handler

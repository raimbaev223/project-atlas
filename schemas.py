"""Tool schemas the model reads."""

from __future__ import annotations

PROJECT_PATH = {
    "type": "string",
    "description": "Absolute path to the project root (the folder that contains its manifest/README).",
}

SCAN = {
    "name": "atlas_scan",
    "description": (
        "Scan a project directory and write .atlas/facts.json. Deterministic and read-only "
        "for the project: manifests, entrypoints, services, routes, docs, TODOs and commits. "
        "Never reads .env, keys, .git internals or .gitignore'd files. Run this before "
        "atlas_plan/atlas_build when the code changed."
    ),
    "parameters": {
        "type": "object",
        "properties": {"project_path": PROJECT_PATH},
        "required": ["project_path"],
    },
}

PLAN = {
    "name": "atlas_plan",
    "description": (
        "Show what a Project Atlas build would read and cost for a project: nodes, files, "
        "bytes, approximate input tokens and model calls. Call this before a mass build and "
        "show the numbers to the user."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "project_path": PROJECT_PATH,
            "scope": {
                "type": "string",
                "description": "'stale' (default), 'all', 'undescribed' or 'node:<id>'.",
            },
        },
        "required": ["project_path"],
    },
}

BUILD = {
    "name": "atlas_build",
    "description": (
        "Build or update <project>/.atlas/atlas.yaml from scanner facts. One structured model "
        "call per node; locked nodes and manual edits are never overwritten. For a full build "
        "with more than one node, first call atlas_plan, show the plan, and pass its 'hash' as "
        "confirm after the user agrees."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "project_path": PROJECT_PATH,
            "scope": {
                "type": "string",
                "description": "'stale' (default), 'all', 'undescribed' or 'node:<id>'.",
            },
            "confirm": {
                "type": "string",
                "description": "Plan hash returned by atlas_plan. Required for scope=all/undescribed with several nodes.",
            },
            "deep": {
                "type": "boolean",
                "description": "Also ask the model to refine the branch list (first build).",
            },
        },
        "required": ["project_path"],
    },
}

STATUS = {
    "name": "atlas_status",
    "description": (
        "Read the current Project Atlas map for a project: the text tree, coverage, stale nodes "
        "whose code changed, and orphans. Does not modify anything."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "project_path": PROJECT_PATH,
            "node_id": {"type": "string", "description": "Optional: return one node's page instead of the whole tree."},
        },
        "required": ["project_path"],
    },
}

TOOLS = [SCAN, PLAN, BUILD, STATUS]
HANDLERS = [schema["name"] for schema in TOOLS]

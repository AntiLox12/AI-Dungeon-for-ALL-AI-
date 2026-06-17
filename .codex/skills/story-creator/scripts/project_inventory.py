#!/usr/bin/env python3
"""Inventory helper for the Simulator Life story-creator skill.

The script is intentionally read-only. It gives the agent a compact snapshot of
the repo before editing prompts, examples, or skill references.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


PROMPT_RE = re.compile(r"prompt_simulator_v(?P<version>\d+)(?:[_-](?P<label>[^.]+))?\.(?P<ext>json|md|txt)$", re.I)
BACKTICK_PATH_RE = re.compile(r"`([^`]+Simulator Life[^`]+)`", re.I)
SYSTEM_TAG_RE = re.compile(r'<system_prompt[^>]*target="([^"]+)"', re.I)
MODEL_LINE_RE = re.compile(r"\*\*Целевая модель:\*\*\s*([^\r\n]+)")


def read_text(path: Path, limit: int | None = None) -> str:
    data = path.read_bytes()
    if limit is not None:
        data = data[:limit]
    return data.decode("utf-8-sig")


def rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def detect_prompt_target(path: Path) -> str | None:
    try:
        text = read_text(path)
    except UnicodeDecodeError:
        return None

    tag = SYSTEM_TAG_RE.search(text)
    if tag:
        return tag.group(1).strip()

    model_line = MODEL_LINE_RE.search(text)
    if model_line:
        return model_line.group(1).strip()

    return None


def detect_prompt_envelope(path: Path) -> dict[str, bool]:
    try:
        text = read_text(path)
    except UnicodeDecodeError:
        return {"utf8": False, "system_tag": False, "identity_tag": False, "recency": False}

    return {
        "utf8": True,
        "system_tag": "<system_prompt" in text,
        "identity_tag": "<identity>" in text,
        "recency": bool(re.search(r"(?i)recency", text)),
    }


def collect_system_prompts(project_root: Path) -> list[dict[str, object]]:
    system_dir = project_root / "System"
    rows: list[dict[str, object]] = []
    if not system_dir.exists():
        return rows

    for path in sorted(system_dir.glob("prompt_simulator_v*.*")):
        match = PROMPT_RE.match(path.name)
        if not match:
            continue
        stat = path.stat()
        rows.append(
            {
                "path": rel(path, project_root),
                "version": int(match.group("version")),
                "label": match.group("label") or "",
                "ext": match.group("ext").lower(),
                "bytes": stat.st_size,
                "modified": int(stat.st_mtime),
                "target": detect_prompt_target(path),
                "envelope": detect_prompt_envelope(path),
            }
        )

    rows.sort(key=lambda row: (row["version"], row["modified"], row["path"]))
    return rows


def collect_content_files(project_root: Path) -> dict[str, object]:
    root_files = [p for p in project_root.glob("*") if p.is_file()]
    avatar_root = [p for p in root_files if "Avatar" in p.name]
    experiments = [p for p in root_files if "Experiment" in p.name]
    scenarios = [p for p in root_files if "Scenario" in p.name]

    nested_avatar_dir = project_root / "Avatars"
    nested_avatars = list(nested_avatar_dir.rglob("*Avatar*.*")) if nested_avatar_dir.exists() else []

    skills_dir = project_root / "Skills"
    skill_files = list(skills_dir.rglob("*.txt")) if skills_dir.exists() else []

    recent = sorted(
        [p for p in root_files if p.suffix.lower() in {".txt", ".json", ".md"}],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[:20]

    return {
        "counts": {
            "root_avatars": len(avatar_root),
            "nested_avatars": len(nested_avatars),
            "experiments": len(experiments),
            "scenarios": len(scenarios),
            "skills": len(skill_files),
        },
        "recent_root_content": [
            {
                "path": rel(p, project_root),
                "bytes": p.stat().st_size,
                "modified": int(p.stat().st_mtime),
            }
            for p in recent
        ],
    }


def collect_example_health(skill_root: Path) -> dict[str, object]:
    index_path = skill_root / "references" / "examples-index.md"
    if not index_path.exists():
        return {"index_found": False, "missing_paths": [], "system_prompts_not_indexed": []}

    text = read_text(index_path)
    raw_paths = sorted(set(match.group(1) for match in BACKTICK_PATH_RE.finditer(text)))
    missing = [p for p in raw_paths if not Path(p).exists()]

    return {
        "index_found": True,
        "referenced_paths": len(raw_paths),
        "missing_paths": missing,
    }


def prompts_missing_from_index(project_root: Path, skill_root: Path, prompts: list[dict[str, object]]) -> list[str]:
    index_path = skill_root / "references" / "examples-index.md"
    if not index_path.exists():
        return []
    text = read_text(index_path)
    return [
        str(project_root / str(row["path"]))
        for row in prompts
        if row["version"] == max([p["version"] for p in prompts], default=0)
        and row["ext"] == "md"
        and Path(str(row["path"])).name not in text
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only inventory for Simulator Life.")
    parser.add_argument("--project-root", default=".", help="Path to the Simulator Life repo.")
    parser.add_argument(
        "--skill-root",
        default=None,
        help="Path to story-creator skill root. Defaults to parent of this script.",
    )
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON.")
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    skill_root = Path(args.skill_root).resolve() if args.skill_root else Path(__file__).resolve().parents[1]

    prompts = collect_system_prompts(project_root)
    latest_version = max([row["version"] for row in prompts], default=None)
    latest_prompts = [row for row in prompts if row["version"] == latest_version] if latest_version is not None else []

    result = {
        "project_root": str(project_root),
        "skill_root": str(skill_root),
        "latest_prompt_version": latest_version,
        "latest_prompt_files": latest_prompts,
        "system_prompt_count": len(prompts),
        "system_prompts": prompts,
        "content": collect_content_files(project_root),
        "examples_index": collect_example_health(skill_root),
        "latest_markdown_prompts_missing_from_examples_index": prompts_missing_from_index(
            project_root, skill_root, prompts
        ),
    }

    sys.stdout.reconfigure(encoding="utf-8")
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2 if args.pretty else None)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

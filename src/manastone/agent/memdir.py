"""File-based persistent memory (memdir).

This is inspired by Claude Code's memdir model, adapted for robots:
- Memories live as Markdown files under a per-robot directory.
- MEMORY.md is an index (one-line hooks), not the memory content itself.
- We keep the system safe and debuggable: files are diffable, auditable, and
  bounded in size.

Design goal (Phase 1):
- Always maintain a stable "robot identity" memory (type=robot_fact).
- Other memory types are supported but are NOT automatically written yet.

See docs/memory-system-design.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import yaml


# ---------------------------------------------------------------------------
# Memory taxonomy (robot-adapted)
# ---------------------------------------------------------------------------

MEMORY_TYPES = {
    "robot_fact",  # stable identity / hardware facts
    "safety_gotcha",  # hard safety boundaries / known failure modes
    "procedure",  # runbooks / SOP
    "preference",  # operator preferences / reporting style
    "incident",  # timestamped case notes
    # Possible future type:
    "service_context",  # who we serve + environment + preference drift
}


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

INDEX_FILENAME = "MEMORY.md"

# Similar caps to Claude Code (keeps index cheap and safe to auto-inject).
MAX_INDEX_LINES = 200
MAX_INDEX_BYTES = 25_000


def get_memdir_root(storage_dir: Path, robot_id: str) -> Path:
    """Return the directory holding file-based memories for a given robot."""
    return storage_dir / "agent_memory" / robot_id / "memories"


def get_memdir_index_path(storage_dir: Path, robot_id: str) -> Path:
    return get_memdir_root(storage_dir, robot_id) / INDEX_FILENAME


# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)


@dataclass(frozen=True)
class MemoryHeader:
    filename: str
    type: Optional[str]
    description: Optional[str]
    updated_at: Optional[str]


def parse_frontmatter(markdown: str) -> Tuple[Dict[str, Any], str]:
    """Parse YAML frontmatter. Returns (frontmatter_dict, body)."""
    m = _FRONTMATTER_RE.match(markdown)
    if not m:
        return {}, markdown
    fm_text, body = m.group(1), m.group(2)
    try:
        fm = yaml.safe_load(fm_text) or {}
        if not isinstance(fm, dict):
            fm = {}
    except Exception:
        fm = {}
    return fm, body


def build_frontmatter(frontmatter: Dict[str, Any]) -> str:
    return "---\n" + yaml.safe_dump(frontmatter, sort_keys=False).strip() + "\n---\n"


# ---------------------------------------------------------------------------
# Index management
# ---------------------------------------------------------------------------

def _truncate_index(text: str) -> str:
    # Line-truncate first.
    lines = text.splitlines()
    if len(lines) > MAX_INDEX_LINES:
        lines = lines[:MAX_INDEX_LINES]
    truncated = "\n".join(lines).strip() + "\n"

    # Then byte-truncate at a newline boundary.
    if len(truncated) > MAX_INDEX_BYTES:
        cut_at = truncated.rfind("\n", 0, MAX_INDEX_BYTES)
        truncated = truncated[: cut_at if cut_at > 0 else MAX_INDEX_BYTES].strip() + "\n"

    return truncated


def ensure_index_exists(index_path: Path) -> None:
    index_path.parent.mkdir(parents=True, exist_ok=True)
    if not index_path.exists():
        index_path.write_text(
            "# MEMORY\n\n"
            "This file is an index. Do not write memory content here.\n"
            "Each entry should be one line: - [Title](file.md) — one-line hook\n\n",
            encoding="utf-8",
        )


def upsert_index_entry(index_path: Path, *, title: str, filename: str, hook: str) -> None:
    """Upsert a one-line entry for filename in MEMORY.md."""
    ensure_index_exists(index_path)
    raw = index_path.read_text(encoding="utf-8")
    lines = raw.splitlines()

    entry = f"- [{title}]({filename}) — {hook}".strip()
    link_pat = re.compile(rf"^\s*-\s*\[[^\]]+\]\({re.escape(filename)}\)\s*—\s*.*$")

    replaced = False
    out: List[str] = []
    for line in lines:
        if link_pat.match(line):
            out.append(entry)
            replaced = True
        else:
            out.append(line)

    if not replaced:
        # Append at end; keep a blank line separation if the file has a header.
        if out and out[-1].strip() != "":
            out.append("")
        out.append(entry)

    new_text = _truncate_index("\n".join(out))
    index_path.write_text(new_text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Filename safety
# ---------------------------------------------------------------------------

_SAFE_FILENAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,80}\.md$")


def sanitize_memory_filename(name: str) -> str:
    """Return a safe, normalized memory filename.

    Allowed: lowercase letters, digits, underscore, hyphen.
    The returned name always ends with `.md`.

    Security note: we intentionally do NOT allow path separators here; callers
    must treat the returned value as a filename only.
    """
    raw = name.strip().lower()

    # Separate stem from extension so we don't accidentally rewrite the `.md`
    # dot into an underscore (e.g. "robot_identity.md" → "robot_identity_md.md").
    if raw.endswith(".md"):
        stem = raw[:-3]
    else:
        stem = raw

    stem = stem.replace(" ", "_")
    stem = re.sub(r"[^a-z0-9_-]", "_", stem)
    stem = re.sub(r"_+", "_", stem).strip("_")

    base = (stem or "memory") + ".md"

    if not _SAFE_FILENAME_RE.match(base):
        base = "memory.md"
    return base


def resolve_memory_path(root: Path, filename: str) -> Path:
    """Resolve filename under root and reject traversal."""
    safe = sanitize_memory_filename(filename)
    path = (root / safe).resolve()
    root_resolved = root.resolve()
    if root_resolved not in path.parents and path != root_resolved:
        raise ValueError("Path traversal detected")
    return path


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------


def scan_memory_headers(root: Path, limit: int = 200) -> List[MemoryHeader]:
    if not root.exists():
        return []
    files = sorted([p for p in root.glob("*.md") if p.name != INDEX_FILENAME])
    headers: List[MemoryHeader] = []

    for p in files[:limit]:
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            continue
        fm, _ = parse_frontmatter(text)
        headers.append(
            MemoryHeader(
                filename=p.name,
                type=str(fm.get("type")) if fm.get("type") is not None else None,
                description=str(fm.get("description")) if fm.get("description") is not None else None,
                updated_at=str(fm.get("updated_at")) if fm.get("updated_at") is not None else None,
            )
        )

    return headers


def format_manifest(headers: Iterable[MemoryHeader]) -> str:
    lines: List[str] = []
    for h in headers:
        t = f"[{h.type}] " if h.type else ""
        desc = f": {h.description}" if h.description else ""
        ua = f" (updated_at={h.updated_at})" if h.updated_at else ""
        lines.append(f"- {t}{h.filename}{ua}{desc}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Deterministic identity memory (Phase 1)
# ---------------------------------------------------------------------------


def build_robot_identity_markdown(robot_id: str, *, config: Any) -> Tuple[Dict[str, Any], str, str]:
    """Return (frontmatter, title, body) for the robot identity memory."""
    now = datetime.now(timezone.utc).isoformat()

    # Best-effort accessors: ManaConfig provides these.
    robot_type = None
    rosbridge = None
    mock_mode = None
    chains = None
    thresholds = None

    try:
        robot_type = config.get_robot_type()
    except Exception:
        pass
    try:
        rosbridge = config.get_rosbridge_url()
    except Exception:
        pass
    try:
        mock_mode = bool(config.is_mock_mode())
    except Exception:
        pass
    try:
        chains = list((config.get_kinematic_chains() or {}).keys())
    except Exception:
        pass
    try:
        thresholds = config.get_thresholds()
    except Exception:
        pass

    frontmatter: Dict[str, Any] = {
        "type": "robot_fact",
        "description": "Stable identity and environment facts for this robot instance (who I am)",
        "robot_id": robot_id,
        "updated_at": now,
    }

    title = f"Robot identity: {robot_id}"

    lines: List[str] = []
    lines.append(f"# {title}")
    lines.append("")
    lines.append("This is the robot's persistent identity record. Keep it factual and stable.")
    lines.append("")
    lines.append("## Identity")
    lines.append(f"- robot_id: {robot_id}")
    if robot_type:
        lines.append(f"- robot_type: {robot_type}")
    if mock_mode is not None:
        lines.append(f"- mock_mode: {mock_mode}")
    if rosbridge:
        lines.append(f"- rosbridge_url: {rosbridge}")

    if chains:
        lines.append("")
        lines.append("## Kinematic chains")
        for c in chains:
            lines.append(f"- {c}")

    if thresholds:
        lines.append("")
        lines.append("## Safety thresholds (from schema)")
        for k, v in thresholds.items():
            lines.append(f"- {k}: {v}")

    lines.append("")
    lines.append("## Notes")
    lines.append("- This file is maintained automatically by Manastone (Phase 1).")
    lines.append("- Future: add service_context, operator preferences, and drift history as separate memories.")

    body = "\n".join(lines).strip() + "\n"
    return frontmatter, title, body


def ensure_robot_identity_memory(storage_dir: Path, robot_id: str, *, config: Any) -> Path:
    """Create/update the robot identity memory file and index entry."""
    root = get_memdir_root(storage_dir, robot_id)
    root.mkdir(parents=True, exist_ok=True)

    filename = "robot_identity.md"
    memory_path = resolve_memory_path(root, filename)

    fm, title, body = build_robot_identity_markdown(robot_id, config=config)
    content = build_frontmatter(fm) + "\n" + body
    memory_path.write_text(content, encoding="utf-8")

    index_path = get_memdir_index_path(storage_dir, robot_id)
    hook = "Who I am: identity, mode, endpoints, and safety thresholds."
    upsert_index_entry(index_path, title=title, filename=memory_path.name, hook=hook)

    return memory_path

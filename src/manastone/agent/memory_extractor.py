"""LLM-assisted memory extraction for the file-based MemDir.

Goal: automatically generate/update persistent memories after agent turns.

Safety model:
- The LLM NEVER writes files directly.
- The LLM returns a structured JSON "write plan" (validated by schema).
- The program applies the plan with strict filename sanitization and confines
  writes to the memdir root.

Behavior notes:
- When the LLM is unavailable (no API key / budget exceeded), extraction
  degrades gracefully (no-op) and never breaks the main agent.
- Phase 1 originally only auto-maintained identity. This module is the
  foundation for auto-enrichment of all memory types.

See docs/memory-system-design.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from manastone.agent.memdir import (
    MEMORY_TYPES,
    build_frontmatter,
    format_manifest,
    get_memdir_index_path,
    get_memdir_root,
    parse_frontmatter,
    resolve_memory_path,
    sanitize_memory_filename,
    scan_memory_headers,
    upsert_index_entry,
)


MEMORY_EXTRACT_SYSTEM_PROMPT = """You are a memory extraction module for a robot operations agent.

Your job is to update the robot's persistent file-based memory store.

Principles:
- Be selective. Only write durable, non-obvious information that will be useful later.
- Prefer UPDATING an existing memory file over creating a duplicate.
- If you are unsure whether something is durable, do not save it.
- Keep each memory focused on ONE topic.
- Do not save secrets (API keys, credentials).

Memory types:
- robot_fact: stable identity/environment facts about this robot instance
- safety_gotcha: hard safety boundaries and known failure modes
- procedure: runbooks / SOPs (repeatable operational playbooks)
- preference: operator/service preferences (report format, risk tolerance)
- incident: timestamped case notes (what happened, symptoms, action, outcome)
- service_context: service objects + environment + preference drift

Output MUST match the provided JSON schema.
"""


MEMORY_PLAN_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "upserts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string"},
                    "filename": {"type": "string"},
                    "title": {"type": "string"},
                    "hook": {"type": "string"},
                    "description": {"type": "string"},
                    "body": {"type": "string"},
                },
                "required": [
                    "type",
                    "filename",
                    "title",
                    "hook",
                    "description",
                    "body",
                ],
                "additionalProperties": False,
            },
        },
        "deletes": {"type": "array", "items": {"type": "string"}},
        "notes": {"type": "string"},
    },
    "required": ["upserts", "deletes"],
    "additionalProperties": False,
}


@dataclass
class MemoryTurnContext:
    robot_id: str
    user_text: str
    result_summary: str
    action: Optional[str] = None
    success: Optional[bool] = None


class MemDirExtractor:
    def __init__(self, robot_id: str, storage_dir: Path, llm_proxy: Any):
        self.robot_id = robot_id
        self.storage_dir = storage_dir
        self.llm_proxy = llm_proxy

    @property
    def root(self) -> Path:
        return get_memdir_root(self.storage_dir, self.robot_id)

    @property
    def index_path(self) -> Path:
        return get_memdir_index_path(self.storage_dir, self.robot_id)

    def _safe_type(self, t: str) -> str:
        if t not in MEMORY_TYPES:
            # Default to incident if the model invents a type.
            return "incident"
        return t

    async def extract_and_apply(self, ctx: MemoryTurnContext) -> Dict[str, Any]:
        """Run LLM extraction and apply the resulting plan.

        Returns a structured summary for logging/debug.
        """
        self.root.mkdir(parents=True, exist_ok=True)

        # Build manifest of existing memories to encourage updates vs duplicates.
        headers = scan_memory_headers(self.root)
        manifest = format_manifest(headers)

        prompt = (
            f"Robot: {ctx.robot_id}\n"
            f"User input: {ctx.user_text}\n\n"
            f"Result summary: {ctx.result_summary}\n\n"
            + (f"Action: {ctx.action}\nSuccess: {ctx.success}\n\n" if ctx.action else "")
            + "Existing memory files:\n"
            + (manifest if manifest else "(none)")
            + "\n\n"
            + "Return a write plan. If nothing is worth saving, return upserts=[] and deletes=[]."
        )

        # If LLM isn't available, degrade gracefully.
        if not self.llm_proxy:
            return {"applied": False, "reason": "no_llm_proxy"}

        try:
            plan = await self.llm_proxy.call_json(
                caller="memory_extractor",
                system_prompt=MEMORY_EXTRACT_SYSTEM_PROMPT,
                user_message=prompt,
                schema=MEMORY_PLAN_SCHEMA,
                inject_memory=False,
                max_tokens=500,
            )
        except Exception as e:
            return {"applied": False, "reason": f"llm_error: {str(e)[:80]}"}

        applied = {"upserts": 0, "deletes": 0}

        # Apply deletes (best-effort)
        for fname in plan.get("deletes", []) or []:
            try:
                path = resolve_memory_path(self.root, fname)
                if path.exists():
                    path.unlink()
                    applied["deletes"] += 1
            except Exception:
                continue

        # Apply upserts
        for item in plan.get("upserts", []) or []:
            try:
                mtype = self._safe_type(str(item["type"]))
                filename = sanitize_memory_filename(str(item["filename"]))
                title = str(item["title"]).strip()
                hook = str(item["hook"]).strip()
                description = str(item["description"]).strip()
                body = str(item["body"]).strip()

                if not title or not body:
                    continue

                path = resolve_memory_path(self.root, filename)

                now = datetime.now(timezone.utc).isoformat()

                # Preserve existing frontmatter fields when updating.
                frontmatter: Dict[str, Any] = {
                    "type": mtype,
                    "description": description,
                    "robot_id": self.robot_id,
                    "updated_at": now,
                }
                if path.exists():
                    try:
                        existing = path.read_text(encoding="utf-8")
                        fm_old, _ = parse_frontmatter(existing)
                        # Keep any additional keys we didn't specify explicitly.
                        for k, v in fm_old.items():
                            if k not in frontmatter:
                                frontmatter[k] = v
                    except Exception:
                        pass

                content = build_frontmatter(frontmatter) + "\n" + f"# {title}\n\n" + body + "\n"
                path.write_text(content, encoding="utf-8")

                # Update index entry
                upsert_index_entry(self.index_path, title=title, filename=path.name, hook=hook)
                applied["upserts"] += 1
            except Exception:
                continue

        return {
            "applied": True,
            "counts": applied,
            "notes": plan.get("notes", ""),
        }

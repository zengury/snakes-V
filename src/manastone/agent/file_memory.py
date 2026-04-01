"""FileMemoryStore — query-time recall of file-based memories.

Phase 1 goal: always keep and surface robot_identity.md (robot_fact).
We provide a simple rule-based recall mechanism that does not require an LLM.

Future: swap in an LLM-based selector using structured outputs.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional, Tuple

from manastone.agent.memdir import (
    INDEX_FILENAME,
    MemoryHeader,
    format_manifest,
    get_memdir_index_path,
    get_memdir_root,
    parse_frontmatter,
    scan_memory_headers,
)


class FileMemoryStore:
    def __init__(self, robot_id: str, storage_dir: Path):
        self.robot_id = robot_id
        self.storage_dir = storage_dir

    @property
    def root(self) -> Path:
        return get_memdir_root(self.storage_dir, self.robot_id)

    @property
    def index_path(self) -> Path:
        return get_memdir_index_path(self.storage_dir, self.robot_id)

    def _read_text_safe(self, path: Path, max_chars: int) -> str:
        try:
            txt = path.read_text(encoding="utf-8")
        except Exception:
            return ""
        return txt[:max_chars]

    def build_recall_context(self, query: str, max_chars: int = 2500) -> str:
        """Build a compact memory context block.

        - Always includes robot_identity.md if available.
        - Adds up to 3 additional best-match memories by keyword overlap.
        """
        if not self.root.exists():
            return ""

        headers = scan_memory_headers(self.root)
        if not headers:
            return ""

        # Always include identity if present.
        identity = [h for h in headers if h.filename == "robot_identity.md"]

        # Select additional memories by simple scoring.
        others = [h for h in headers if h.filename != "robot_identity.md"]
        selected = identity + self._select_by_overlap(query, others, k=3)

        parts: List[str] = []
        parts.append("=== FILE MEMORIES (persistent) ===")

        # Include an ultra-compact manifest (helps the model know what's available).
        manifest = format_manifest(selected)
        if manifest:
            parts.append("Selected memory files:")
            parts.append(manifest)

        # Include content excerpts.
        for h in selected:
            p = self.root / h.filename
            text = self._read_text_safe(p, max_chars=1800)
            if not text:
                continue
            fm, body = parse_frontmatter(text)
            mem_type = fm.get("type")
            desc = fm.get("description")
            parts.append("")
            parts.append(f"--- {h.filename} ({mem_type}) ---")
            if desc:
                parts.append(f"description: {desc}")
            # Prefer body only (skip frontmatter noise)
            parts.append(body.strip()[:1200])

        result = "\n".join(parts).strip() + "\n"
        return result[:max_chars]

    def _select_by_overlap(
        self, query: str, headers: List[MemoryHeader], k: int
    ) -> List[MemoryHeader]:
        q = (query or "").lower()
        q_tokens = set(re.findall(r"[a-z0-9_\-]+", q))

        def score(h: MemoryHeader) -> Tuple[int, int]:
            # (overlap_count, recency_hint)
            text = (h.filename + " " + (h.description or "") + " " + (h.type or "")).lower()
            tokens = set(re.findall(r"[a-z0-9_\-]+", text))
            overlap = len(q_tokens & tokens)
            recency = 0
            if h.updated_at:
                recency = 1
            return (overlap, recency)

        ranked = sorted(headers, key=score, reverse=True)
        return ranked[:k]

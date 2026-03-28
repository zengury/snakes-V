import json
from datetime import datetime
from pathlib import Path
from typing import List, Optional


class ModelZoo:
    """Cross-robot shared model repository."""

    def __init__(self, base_dir: Path = Path("storage/knowledge_base/model_zoo")):
        self._base = base_dir

    def publish(
        self,
        model_type: str,
        model_data: bytes,
        source_robot: str,
        source_profile: str,
        version: str,
        metadata: dict,
    ) -> str:
        """Publish a model. Returns filename."""
        d = self._base / model_type
        d.mkdir(parents=True, exist_ok=True)

        filename = f"{model_type}_{source_profile}_v{version}.bin"
        (d / filename).write_bytes(model_data)

        meta = {
            "filename": filename,
            "model_type": model_type,
            "source_robot": source_robot,
            "source_profile": source_profile,
            "version": version,
            "published_at": datetime.now().isoformat(),
            "training_samples": metadata.get("samples", 0),
            "confidence": metadata.get("confidence", 0.0),
            **{k: v for k, v in metadata.items() if k not in ("samples", "confidence")},
        }
        (d / f"{filename}.meta.json").write_text(json.dumps(meta, indent=2))
        return filename

    def query(self, model_type: str, profile: Optional[str] = None) -> List[dict]:
        """List available models sorted by confidence descending."""
        d = self._base / model_type
        if not d.exists():
            return []
        results = []
        for meta_file in d.glob("*.meta.json"):
            try:
                meta = json.loads(meta_file.read_text())
                if profile and meta.get("source_profile") != profile:
                    continue
                results.append(meta)
            except Exception:
                continue
        return sorted(results, key=lambda m: m.get("confidence", 0.0), reverse=True)

    def load(self, model_type: str, filename: str) -> bytes:
        return (self._base / model_type / filename).read_bytes()

    def list_model_types(self) -> List[str]:
        if not self._base.exists():
            return []
        return [d.name for d in self._base.iterdir() if d.is_dir()]

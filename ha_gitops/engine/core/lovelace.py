"""Convert storage-mode Lovelace dashboards to YAML mode.

Reads ``.storage/lovelace*`` **read-only** and produces YAML files plus a
``lovelace:`` block for configuration.yaml. It never touches ``.storage``, so the
original UI dashboards stay intact and the whole thing is reversible (remove the
block / ``mode: yaml`` to fall back to storage mode).
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

log = logging.getLogger("ha_gitops.lovelace")


@dataclass(slots=True)
class DashFile:
    relpath: str
    content: str


@dataclass(slots=True)
class Conversion:
    files: list[DashFile] = field(default_factory=list)
    lovelace_block: str = ""
    dashboards: list[str] = field(default_factory=list)
    resources: int = 0
    has_default: bool = False
    warnings: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.files and not self.lovelace_block

    def to_dict(self) -> dict:
        return {
            "files": [f.relpath for f in self.files],
            "dashboards": self.dashboards,
            "resources": self.resources,
            "has_default": self.has_default,
            "warnings": self.warnings,
            "empty": self.empty,
        }


def _read(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _data(obj: dict | None) -> dict:
    d = (obj or {}).get("data")
    return d if isinstance(d, dict) else {}


def _dump(config: dict) -> str:
    return yaml.safe_dump(config, sort_keys=False, default_flow_style=False, allow_unicode=True)


def _safe_filename(url_path: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", url_path)


def detect(ha_dir: Path) -> dict:
    """Lightweight summary of storage-mode dashboards on the live instance."""
    storage = Path(ha_dir) / ".storage"
    out = {"default": False, "dashboards": [], "resources": 0}
    if not storage.is_dir():
        return out
    out["default"] = (storage / "lovelace").is_file()
    for item in _data(_read(storage / "lovelace_dashboards")).get("items", []) or []:
        if isinstance(item, dict) and item.get("mode", "storage") == "storage":
            out["dashboards"].append(item.get("url_path") or item.get("id"))
    out["resources"] = len(_data(_read(storage / "lovelace_resources")).get("items", []) or [])
    return out


def build(ha_dir: Path) -> Conversion:
    """Build the conversion plan (no files written)."""
    storage = Path(ha_dir) / ".storage"
    conv = Conversion()
    if not storage.is_dir():
        conv.warnings.append("No .storage directory found on this instance.")
        return conv

    lovelace: dict = {}

    # Default ("Overview") dashboard -> ui-lovelace.yaml
    default_config = _data(_read(storage / "lovelace")).get("config")
    if isinstance(default_config, dict):
        conv.files.append(DashFile("ui-lovelace.yaml", _dump(default_config)))
        lovelace["mode"] = "yaml"
        conv.has_default = True

    # Custom-card resources
    resources = [
        {"url": r["url"], "type": r.get("type", "module")}
        for r in _data(_read(storage / "lovelace_resources")).get("items", []) or []
        if isinstance(r, dict) and r.get("url")
    ]
    if resources:
        lovelace["resources"] = resources
        conv.resources = len(resources)

    # Named dashboards -> dashboards/<slug>.yaml + registry entries
    dashboards: dict = {}
    for item in _data(_read(storage / "lovelace_dashboards")).get("items", []) or []:
        if not isinstance(item, dict) or item.get("mode", "storage") != "storage":
            continue
        url_path = item.get("url_path")
        dash_id = item.get("id")
        title = item.get("title") or url_path or dash_id
        if not url_path:
            conv.warnings.append(f"Dashboard '{dash_id}' has no URL path; skipped.")
            continue

        cfg = None
        for key in (dash_id, url_path):
            if key:
                cfg = _data(_read(storage / f"lovelace.{key}")).get("config")
                if isinstance(cfg, dict):
                    break
        if not isinstance(cfg, dict):
            conv.warnings.append(f"Could not read the config for dashboard '{title}'; skipped.")
            continue

        # HA requires a hyphen in YAML-mode dashboard URL paths.
        key = url_path if "-" in url_path else f"{url_path}-dashboard"
        if key != url_path:
            conv.warnings.append(
                f"'{title}': URL changed to /{key} (HA requires a hyphen in YAML dashboard URLs)."
            )

        fname = f"dashboards/{_safe_filename(key)}.yaml"
        conv.files.append(DashFile(fname, _dump(cfg)))
        entry = {"mode": "yaml", "title": title, "filename": fname}
        if item.get("icon"):
            entry["icon"] = item["icon"]
        entry["show_in_sidebar"] = bool(item.get("show_in_sidebar", True))
        if item.get("require_admin"):
            entry["require_admin"] = True
        dashboards[key] = entry
        conv.dashboards.append(url_path)

    if dashboards:
        lovelace["dashboards"] = dashboards
    if lovelace:
        conv.lovelace_block = _dump({"lovelace": lovelace})
    return conv


def apply(conv: Conversion, config_root: Path) -> list[str]:
    """Write the planned files and append the lovelace block to configuration.yaml."""
    config_root = Path(config_root)
    written: list[str] = []
    for f in conv.files:
        target = config_root / f.relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f.content, encoding="utf-8")
        written.append(f.relpath)

    if conv.lovelace_block:
        cfg = config_root / "configuration.yaml"
        existing = cfg.read_text(encoding="utf-8") if cfg.is_file() else ""
        if re.search(r"(?m)^lovelace:", existing):
            conv.warnings.append(
                "configuration.yaml already has a 'lovelace:' key — left unchanged; "
                "merge the block manually."
            )
        else:
            sep = "" if (existing == "" or existing.endswith("\n")) else "\n"
            cfg.write_text(
                existing + sep + "\n# Added by HA-GitOps dashboard converter\n" + conv.lovelace_block,
                encoding="utf-8",
            )
            written.append("configuration.yaml")
    return written

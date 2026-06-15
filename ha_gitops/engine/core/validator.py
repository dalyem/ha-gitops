"""Layer-1 validation: YAML well-formedness + !include / !secret resolution.

This runs against an *isolated* checkout in ``/data`` (never the live config) so most
breakage is caught before anything touches Home Assistant. Home Assistant's own
``check_config`` (Layer 2) remains the authoritative gate before any restart.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from ..models import ValidationResult


def _make_loader(basedir: Path, secret_keys: set[str], includes: list[str], errors: list[str], visited: set[Path]):
    class _HALoader(yaml.SafeLoader):
        pass

    def _resolve_include(node_value: str) -> Path:
        return (basedir / node_value).resolve()

    def include(loader: yaml.SafeLoader, node):  # !include file.yaml
        fn = loader.construct_scalar(node)
        target = _resolve_include(fn)
        includes.append(str(target))
        if target.is_file():
            _load_file(target, secret_keys, includes, errors, visited)
            return {}
        errors.append(f"!include target not found: {fn}")
        return {}

    def include_dir(loader: yaml.SafeLoader, node):  # !include_dir_* dir
        dirname = loader.construct_scalar(node)
        target = _resolve_include(dirname)
        includes.append(str(target))
        if not target.is_dir():
            errors.append(f"!include_dir target not found: {dirname}")
        return []

    def secret(loader: yaml.SafeLoader, node):  # !secret key
        secret_keys.add(loader.construct_scalar(node))
        return ""

    def passthrough(loader: yaml.SafeLoader, node):  # !env_var, !input, etc.
        return ""

    def unknown(loader: yaml.SafeLoader, tag_suffix: str, node):
        return None

    _HALoader.add_constructor("!include", include)
    for tag in (
        "!include_dir_list",
        "!include_dir_merge_list",
        "!include_dir_named",
        "!include_dir_merge_named",
    ):
        _HALoader.add_constructor(tag, include_dir)
    _HALoader.add_constructor("!secret", secret)
    _HALoader.add_constructor("!env_var", passthrough)
    _HALoader.add_constructor("!input", passthrough)
    _HALoader.add_multi_constructor("!", unknown)
    return _HALoader


def _load_file(
    path: Path,
    secret_keys: set[str],
    includes: list[str],
    errors: list[str],
    visited: set[Path],
) -> None:
    path = path.resolve()
    if path in visited:
        return
    visited.add(path)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        errors.append(f"{path.name}: cannot read ({exc})")
        return
    loader_cls = _make_loader(path.parent, secret_keys, includes, errors, visited)
    try:
        yaml.load(text, Loader=loader_cls)  # noqa: S506 - custom safe loader
    except yaml.YAMLError as exc:
        errors.append(f"{path.name}: {_fmt_yaml_error(exc)}")


def _fmt_yaml_error(exc: yaml.YAMLError) -> str:
    mark = getattr(exc, "problem_mark", None)
    problem = getattr(exc, "problem", None) or str(exc)
    if mark is not None:
        return f"{problem} (line {mark.line + 1}, column {mark.column + 1})"
    return str(exc).replace("\n", " ")


def _load_secret_keys(secrets_path: Path | None) -> set[str] | None:
    if not secrets_path or not secrets_path.is_file():
        return None
    try:
        data = yaml.safe_load(secrets_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return set()
    return set(data.keys()) if isinstance(data, dict) else set()


def validate_config_dir(config_dir: Path, secrets_path: Path | None) -> ValidationResult:
    config_dir = Path(config_dir)
    errors: list[str] = []
    warnings: list[str] = []
    secret_keys: set[str] = set()
    includes: list[str] = []
    visited: set[Path] = set()

    if not (config_dir / "configuration.yaml").is_file():
        errors.append("configuration.yaml not found at the config path.")
        return ValidationResult(ok=False, errors=errors)

    # Parse every YAML file (entry point + standalone) to catch all syntax errors.
    yaml_files = sorted(
        p for p in config_dir.rglob("*")
        if p.suffix in (".yaml", ".yml") and ".git" not in p.parts and ".storage" not in p.parts
    )
    for path in yaml_files:
        _load_file(path, secret_keys, includes, errors, visited)

    # Verify referenced secrets exist.
    available = _load_secret_keys(secrets_path)
    if secret_keys:
        if available is None:
            errors.append(
                f"{len(secret_keys)} !secret reference(s) but secrets.yaml is not available "
                "for validation."
            )
        else:
            missing = sorted(secret_keys - available)
            if missing:
                errors.append("Missing secrets: " + ", ".join(missing[:20]))

    return ValidationResult(ok=not errors, errors=errors, warnings=warnings)

"""Layer-1 validation: YAML well-formedness + !include / !secret resolution.

This runs against an *isolated* checkout in ``/data`` (never the live config) so most
breakage is caught before anything touches Home Assistant. Home Assistant's own
``check_config`` (Layer 2) remains the authoritative gate before any restart.

An ``!include`` / ``!include_dir`` target that is missing from the repo but present on
the **live** instance (e.g. an empty ``themes/`` dir that git can't store, or a
gitignored file) is not a failure: the deploy never removes untracked live files, so
those targets still exist at runtime.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from ..models import ValidationResult


class _Ctx:
    __slots__ = (
        "secret_keys", "includes", "errors", "warnings", "visited",
        "config_root", "live_root",
    )

    def __init__(self, config_root: Path, live_root: Path | None) -> None:
        self.secret_keys: set[str] = set()
        self.includes: list[str] = []
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.visited: set[Path] = set()
        self.config_root = config_root
        self.live_root = live_root

    def exists_in_live(self, target: Path, want_dir: bool = False) -> bool:
        """Does ``target`` (under the staging config root) exist under the live root?"""
        if not self.live_root:
            return False
        try:
            rel = target.relative_to(self.config_root)
        except ValueError:
            return False
        live = self.live_root / rel
        return live.is_dir() if want_dir else live.exists()


def _make_loader(basedir: Path, ctx: _Ctx):
    class _HALoader(yaml.SafeLoader):
        pass

    def _resolve(node_value: str) -> Path:
        return (basedir / node_value).resolve()

    def include(loader: yaml.SafeLoader, node):  # !include file.yaml
        fn = loader.construct_scalar(node)
        target = _resolve(fn)
        ctx.includes.append(str(target))
        if target.is_file():
            _load_file(target, ctx)
        elif not ctx.exists_in_live(target):
            ctx.errors.append(f"!include target not found: {fn}")
        return {}

    def include_dir(loader: yaml.SafeLoader, node):  # !include_dir_* dir
        dirname = loader.construct_scalar(node)
        target = _resolve(dirname)
        ctx.includes.append(str(target))
        if not target.is_dir() and not ctx.exists_in_live(target, want_dir=True):
            # Not fatal: an empty/optional dir; HA's check_config (Layer 2) is the
            # authoritative gate on the live instance.
            ctx.warnings.append(f"!include_dir target not found: {dirname}")
        return []

    def secret(loader: yaml.SafeLoader, node):  # !secret key
        ctx.secret_keys.add(loader.construct_scalar(node))
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


def _load_file(path: Path, ctx: _Ctx) -> None:
    path = path.resolve()
    if path in ctx.visited:
        return
    ctx.visited.add(path)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        ctx.errors.append(f"{path.name}: cannot read ({exc})")
        return
    loader_cls = _make_loader(path.parent, ctx)
    try:
        yaml.load(text, Loader=loader_cls)  # noqa: S506 - custom safe loader
    except yaml.YAMLError as exc:
        ctx.errors.append(f"{path.name}: {_fmt_yaml_error(exc)}")


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
        # Malformed secrets.yaml — treat as unavailable so validation reports the
        # real problem rather than a list of "missing" individual secrets.
        return None
    return set(data.keys()) if isinstance(data, dict) else set()


def validate_config_dir(
    config_dir: Path, secrets_path: Path | None, live_dir: Path | None = None
) -> ValidationResult:
    config_dir = Path(config_dir).resolve()
    ctx = _Ctx(config_root=config_dir, live_root=Path(live_dir).resolve() if live_dir else None)

    if not (config_dir / "configuration.yaml").is_file():
        return ValidationResult(ok=False, errors=["configuration.yaml not found at the config path."])

    # Parse every YAML file (entry point + standalone) to catch all syntax errors.
    yaml_files = sorted(
        p for p in config_dir.rglob("*")
        if p.suffix in (".yaml", ".yml") and ".git" not in p.parts and ".storage" not in p.parts
    )
    for path in yaml_files:
        _load_file(path, ctx)

    # Verify referenced secrets exist.
    available = _load_secret_keys(secrets_path)
    if ctx.secret_keys:
        if available is None:
            ctx.errors.append(
                f"{len(ctx.secret_keys)} !secret reference(s) but secrets.yaml is not "
                "available for validation."
            )
        else:
            missing = sorted(ctx.secret_keys - available)
            if missing:
                ctx.errors.append("Missing secrets: " + ", ".join(missing[:20]))

    return ValidationResult(ok=not ctx.errors, errors=ctx.errors, warnings=ctx.warnings)

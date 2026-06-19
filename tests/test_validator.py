from engine.core import validator


def _write(d, name, text):
    p = d / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def test_valid_config(tmp_path):
    _write(tmp_path, "configuration.yaml", "homeassistant:\n  name: Home\nscript: !include scripts.yaml\napi_password: !secret pw\n")
    _write(tmp_path, "scripts.yaml", "{}\n")
    secrets = _write(tmp_path, "secrets.yaml", "pw: hunter2\n")
    result = validator.validate_config_dir(tmp_path, secrets)
    assert result.ok, result.errors


def test_missing_configuration(tmp_path):
    result = validator.validate_config_dir(tmp_path, None)
    assert not result.ok
    assert any("configuration.yaml" in e for e in result.errors)


def test_invalid_yaml(tmp_path):
    _write(tmp_path, "configuration.yaml", 'foo: "unterminated\nbar: baz\n')  # bad scalar
    result = validator.validate_config_dir(tmp_path, None)
    assert not result.ok


def test_missing_secret(tmp_path):
    _write(tmp_path, "configuration.yaml", "api_password: !secret nope\n")
    secrets = _write(tmp_path, "secrets.yaml", "other: x\n")
    result = validator.validate_config_dir(tmp_path, secrets)
    assert not result.ok
    assert any("nope" in e for e in result.errors)


def test_missing_include(tmp_path):
    _write(tmp_path, "configuration.yaml", "script: !include missing.yaml\n")
    result = validator.validate_config_dir(tmp_path, None)
    assert not result.ok
    assert any("missing.yaml" in e for e in result.errors)


def test_missing_include_dir_is_warning_not_error(tmp_path):
    # An empty themes/ dir can't be stored by git; missing it must not block deploy.
    _write(tmp_path, "configuration.yaml", "frontend:\n  themes: !include_dir_merge_named themes\n")
    result = validator.validate_config_dir(tmp_path, None)
    assert result.ok
    assert any("themes" in w for w in result.warnings)


def test_include_dir_present_on_live_passes(tmp_path):
    cfg, live = tmp_path / "cfg", tmp_path / "live"
    _write(cfg, "configuration.yaml", "frontend:\n  themes: !include_dir_merge_named themes\n")
    (live / "themes").mkdir(parents=True)  # empty themes/ exists on the live instance
    result = validator.validate_config_dir(cfg, None, live_dir=live)
    assert result.ok and not result.warnings


def test_include_file_present_on_live_passes(tmp_path):
    cfg, live = tmp_path / "cfg", tmp_path / "live"
    _write(cfg, "configuration.yaml", "device_tracker: !include known_devices.yaml\n")
    _write(live, "known_devices.yaml", "{}\n")  # gitignored file lives only on the instance
    result = validator.validate_config_dir(cfg, None, live_dir=live)
    assert result.ok


def test_unknown_tag_is_tolerated(tmp_path):
    _write(tmp_path, "configuration.yaml", "template:\n  value: !input my_input\n")
    result = validator.validate_config_dir(tmp_path, None)
    assert result.ok, result.errors


def test_malformed_secrets_file_is_reported(tmp_path):
    _write(tmp_path, "configuration.yaml", "api_password: !secret foo\n")
    secrets = _write(tmp_path, "secrets.yaml", "foo: [unclosed\n")
    result = validator.validate_config_dir(tmp_path, secrets)
    assert not result.ok
    # The broken secrets file is surfaced rather than a misleading "missing foo".
    assert any("secrets.yaml" in e for e in result.errors)

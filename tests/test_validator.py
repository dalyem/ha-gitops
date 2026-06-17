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

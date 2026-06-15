import logging

from engine.core import secrets_store
from engine.core.options import Options, parse_interval


def test_parse_interval():
    assert parse_interval("5m") == 300
    assert parse_interval("90s") == 90
    assert parse_interval("2h") == 7200
    assert parse_interval("30") == 1800        # bare number = minutes
    assert parse_interval("garbage") == 300    # default
    assert parse_interval("1s") == 30          # clamped to MIN_INTERVAL
    assert parse_interval("999d") == 86400     # clamped to MAX_INTERVAL


def test_options_defaults_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr("engine.settings.OPTIONS_FILE", tmp_path / "nope.json")
    opts = Options.load()
    assert opts.check_interval == "5m"
    assert opts.interval_seconds == 300
    assert opts.backup_before_deploy is True


def test_options_loaded_from_file(tmp_path, monkeypatch):
    f = tmp_path / "options.json"
    f.write_text('{"check_interval": "15m", "auto_deploy": false}')
    monkeypatch.setattr("engine.settings.OPTIONS_FILE", f)
    opts = Options.load()
    assert opts.interval_seconds == 900
    assert opts.auto_deploy is False


def test_token_redaction(tmp_path, monkeypatch):
    monkeypatch.setattr("engine.settings.DATA_DIR", tmp_path)
    monkeypatch.setattr("engine.settings.CREDENTIALS_FILE", tmp_path / ".credentials")
    token = "github_pat_super_secret_value_1234"
    secrets_store.save_token(token)
    assert secrets_store.load_token() == token
    assert token not in secrets_store.redact(f"cloning with {token} now")
    assert "***redacted***" in secrets_store.redact(token)

    rec = logging.LogRecord("x", logging.INFO, __file__, 1, f"auth {token}", None, None)
    secrets_store.RedactingFilter().filter(rec)
    assert token not in rec.getMessage()

    secrets_store.clear_token()
    assert secrets_store.load_token() is None

import json

from engine.core import lovelace


def _storage(ha, files: dict):
    s = ha / ".storage"
    s.mkdir(parents=True, exist_ok=True)
    for name, obj in files.items():
        (s / name).write_text(json.dumps(obj))


def test_detect_and_build(tmp_path):
    _storage(tmp_path, {
        "lovelace": {"data": {"config": {"title": "Home",
                    "views": [{"title": "Main", "cards": [{"type": "markdown", "content": "hi"}]}]}}},
        "lovelace_dashboards": {"data": {"items": [
            {"id": "abc", "url_path": "climate-control", "title": "Climate Control",
             "icon": "mdi:thermostat", "mode": "storage", "show_in_sidebar": True},
            {"id": "xyz", "url_path": "kitchen", "title": "Kitchen",
             "icon": "mdi:fridge", "mode": "storage", "show_in_sidebar": True},
        ]}},
        "lovelace.abc": {"data": {"config": {"views": [{"title": "CC", "cards": []}]}}},
        "lovelace.xyz": {"data": {"config": {"views": [{"title": "K", "cards": []}]}}},
        "lovelace_resources": {"data": {"items": [
            {"url": "/hacsfiles/Bubble-Card/bubble-card.js", "type": "module"}]}},
    })

    d = lovelace.detect(tmp_path)
    assert d["default"] is True
    assert set(d["dashboards"]) == {"climate-control", "kitchen"}
    assert d["resources"] == 1

    conv = lovelace.build(tmp_path)
    rels = {f.relpath for f in conv.files}
    assert "ui-lovelace.yaml" in rels
    assert "dashboards/climate-control.yaml" in rels      # hyphenated URL preserved
    assert "dashboards/kitchen-dashboard.yaml" in rels    # hyphen added for "kitchen"
    assert conv.resources == 1 and conv.has_default
    assert "mode: yaml" in conv.lovelace_block
    assert "/hacsfiles/Bubble-Card/bubble-card.js" in conv.lovelace_block
    assert any("kitchen" in w.lower() for w in conv.warnings)  # URL-change warning


def test_apply_writes_files_and_appends(tmp_path):
    _storage(tmp_path, {
        "lovelace": {"data": {"config": {"views": [{"cards": []}]}}},
        "lovelace_resources": {"data": {"items": []}},
        "lovelace_dashboards": {"data": {"items": []}},
    })
    (tmp_path / "configuration.yaml").write_text("default_config:\n")
    conv = lovelace.build(tmp_path)
    written = lovelace.apply(conv, tmp_path)
    assert "ui-lovelace.yaml" in written and (tmp_path / "ui-lovelace.yaml").is_file()
    cfg = (tmp_path / "configuration.yaml").read_text()
    assert "default_config:" in cfg                       # original preserved
    assert "lovelace:" in cfg and "mode: yaml" in cfg


def test_apply_respects_existing_lovelace(tmp_path):
    _storage(tmp_path, {"lovelace": {"data": {"config": {"views": []}}}})
    (tmp_path / "configuration.yaml").write_text("lovelace:\n  mode: yaml\n")
    conv = lovelace.build(tmp_path)
    lovelace.apply(conv, tmp_path)
    cfg = (tmp_path / "configuration.yaml").read_text()
    assert cfg.count("lovelace:") == 1                    # not duplicated
    assert any("already has" in w for w in conv.warnings)


def test_build_no_storage(tmp_path):
    conv = lovelace.build(tmp_path)
    assert conv.empty and conv.warnings

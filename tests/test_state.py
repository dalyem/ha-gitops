from engine.core.state import State


def test_pointers(tmp_path):
    st = State(tmp_path / "s.db")
    assert st.last_deployed_sha is None
    st.last_deployed_sha = "abc"
    st.sync_base_sha = "abc"
    assert st.get("last_deployed_sha") == "abc"
    st.set_manifest({"a.yaml": "hash"})
    assert st.get_manifest() == {"a.yaml": "hash"}
    assert st.monitoring_enabled is True
    st.monitoring_enabled = False
    assert st.monitoring_enabled is False
    st.close()


def test_deployment_history(tmp_path):
    st = State(tmp_path / "s.db")
    did = st.record_deployment("pull", "deadbeef", "main", "in_progress")
    st.finish_deployment(did, status="success", files_changed=3, restarted=True,
                         message="ok", errors=[])
    rows = st.list_deployments()
    assert rows[0]["status"] == "success"
    assert rows[0]["restarted"] is True
    assert rows[0]["files_changed"] == 3
    st.close()


def test_conflict_dedup(tmp_path):
    st = State(tmp_path / "s.db")
    id1 = st.record_conflict("base", "remote1", "1 modified")
    id2 = st.record_conflict("base", "remote1", "1 modified")  # same remote -> dedup
    assert id1 == id2
    assert st.get_open_conflict()["remote_sha"] == "remote1"
    st.resolve_conflict(id1, "push")
    assert st.get_open_conflict() is None
    st.close()


def test_readiness_roundtrip(tmp_path):
    st = State(tmp_path / "s.db")
    st.record_readiness({"score": 80, "is_valid_repo": True, "is_empty": False,
                         "has_blockers": False, "deployable": True, "findings": []})
    latest = st.latest_readiness()
    assert latest["score"] == 80
    assert latest["deployable"] is True
    st.close()

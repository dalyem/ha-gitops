from engine.core import readiness
from engine.models import Severity

CLEAN = [
    "configuration.yaml",
    "automations.yaml",
    "scripts.yaml",
    ".gitignore",
    "secrets.yaml.example",
    "README.md",
]
GOOD_GITIGNORE = "secrets.yaml\n.storage/\n*.db\n*.log\n"


def codes(report):
    return {f.code for f in report.findings}


def test_clean_repo_is_deployable():
    report = readiness.analyze(CLEAN, GOOD_GITIGNORE, is_empty=False)
    assert report.is_valid_repo
    assert report.deployable
    assert not report.has_blockers
    assert report.score >= 90


def test_empty_repo():
    report = readiness.analyze([], None, is_empty=True)
    assert report.is_empty
    assert not report.is_valid_repo
    assert "empty_repo" in codes(report)


def test_committed_secrets_block_deploy():
    report = readiness.analyze(CLEAN + ["secrets.yaml"], GOOD_GITIGNORE, is_empty=False)
    assert report.has_blockers
    assert not report.deployable
    assert "secrets_committed" in codes(report)


def test_committed_database_blocks():
    report = readiness.analyze(CLEAN + ["home-assistant_v2.db"], GOOD_GITIGNORE, is_empty=False)
    assert "database_committed" in codes(report)
    assert report.has_blockers


def test_committed_storage_blocks():
    report = readiness.analyze(CLEAN + [".storage/auth"], GOOD_GITIGNORE, is_empty=False)
    assert "storage_committed" in codes(report)


def test_missing_configuration_is_blocker():
    report = readiness.analyze(["automations.yaml"], GOOD_GITIGNORE, is_empty=False)
    assert not report.is_valid_repo
    assert "missing_configuration_yaml" in codes(report)


def test_missing_gitignore_warns():
    report = readiness.analyze(["configuration.yaml"], None, is_empty=False)
    findings = {f.code: f.severity for f in report.findings}
    assert findings.get("missing_gitignore") is Severity.WARNING
    # missing gitignore is only a warning, so a bare-but-valid repo still deploys
    assert report.deployable


def test_weak_gitignore_warns():
    report = readiness.analyze(CLEAN, "*.log\n", is_empty=False)
    assert "weak_gitignore" in codes(report)


def test_gitignore_comment_does_not_satisfy_critical():
    # secrets.yaml and .storage/ appear only as comments -> still "missing".
    gi = "# secrets.yaml\n# .storage/\n*.db\n"
    report = readiness.analyze(CLEAN, gi, is_empty=False)
    assert "weak_gitignore" in codes(report)


def test_conf_files_are_not_flagged_as_tokens():
    report = readiness.analyze(CLEAN + ["nginx.conf"], GOOD_GITIGNORE, is_empty=False)
    assert "tokens_committed" not in codes(report)

import subprocess

import pytest
import scheduled_deploy_gate
from scheduled_deploy_gate import COMPARE_FILE_LIMIT, GateDecision, decide, latest_release_sha, recheck

RUN_URL = "https://github.com/dimagi/commcare-connect/actions/runs/1"


def ci_run(sha, status="completed", conclusion="success"):
    return {"headSha": sha, "status": status, "conclusion": conclusion, "url": RUN_URL}


@pytest.mark.parametrize(
    "head, release, run, paths, deploy, warn, reason_part",
    [
        pytest.param("a" * 40, "a" * 40, ci_run("a" * 40), [], False, False, "nothing to deploy", id="nothing-new"),
        pytest.param("b" * 40, "a" * 40, ci_run("b" * 40), [], True, False, "", id="green"),
        pytest.param(
            "b" * 40, "a" * 40, ci_run("b" * 40, conclusion="failure"), [], False, True, "failure", id="failed"
        ),
        pytest.param(
            "b" * 40,
            "a" * 40,
            ci_run("b" * 40, status="in_progress", conclusion=None),
            [],
            False,
            True,
            "in_progress",
            id="in-progress",
        ),
        pytest.param("b" * 40, "a" * 40, None, [], False, True, "no CI run", id="no-run"),
        pytest.param("c" * 40, "a" * 40, ci_run("b" * 40), ["docs/x.md"], True, False, "", id="docs-only-since-run"),
        pytest.param(
            "c" * 40,
            "a" * 40,
            ci_run("b" * 40),
            ["docs/x.md", "commcare_connect/a.py"],
            False,
            True,
            "not been run",
            id="code-since-run",
        ),
        pytest.param("b" * 40, None, ci_run("b" * 40), [], True, False, "", id="no-release-yet"),
        pytest.param(
            "c" * 40,
            "a" * 40,
            ci_run("b" * 40),
            ["docs/x.md"] * COMPARE_FILE_LIMIT,
            False,
            True,
            "too many files",
            id="compare-truncated",
        ),
        # main moved between reading HEAD and reading the CI run: the compare comes back empty
        pytest.param("b" * 40, "a" * 40, ci_run("c" * 40), [], False, True, "not been run", id="run-for-other-sha"),
    ],
)
def test_decide(head, release, run, paths, deploy, warn, reason_part):
    decision = decide(head, release, run, paths)

    assert isinstance(decision, GateDecision)
    assert (decision.deploy, decision.warn) == (deploy, warn)
    assert reason_part in decision.reason


@pytest.mark.parametrize(
    "run, paths",
    [
        pytest.param(ci_run("b" * 40, conclusion="failure"), [], id="failed"),
        pytest.param(ci_run("b" * 40, status="queued", conclusion=None), [], id="queued"),
        pytest.param(ci_run("a" * 40), ["config/settings/base.py"], id="code-since-run"),
    ],
)
def test_warning_names_commit_and_run(run, paths):
    decision = decide("b" * 40, "0" * 40, run, paths)

    assert decision.warn
    assert "bbbbbbb" in decision.reason
    assert RUN_URL in decision.reason


def test_warning_without_run_names_commit():
    decision = decide("b" * 40, "0" * 40, None, [])

    assert "bbbbbbb" in decision.reason


def test_latest_release_sha_none_when_repo_has_no_release(monkeypatch):
    def gh(*args):
        raise subprocess.CalledProcessError(1, ["gh", *args], stderr="gh: Not Found (HTTP 404)")

    monkeypatch.setattr(scheduled_deploy_gate, "_gh", gh)

    assert latest_release_sha("dimagi/commcare-connect") is None


def test_latest_release_sha_raises_on_other_errors(monkeypatch):
    def gh(*args):
        raise subprocess.CalledProcessError(1, ["gh", *args], stderr="gh: API rate limit exceeded (HTTP 403)")

    monkeypatch.setattr(scheduled_deploy_gate, "_gh", gh)

    with pytest.raises(subprocess.CalledProcessError):
        latest_release_sha("dimagi/commcare-connect")


@pytest.mark.parametrize(
    "compare_status, deploy",
    [("ahead", True), ("identical", False), ("behind", False), ("diverged", False)],
)
def test_recheck_deploys_only_commits_ahead_of_the_release(compare_status, deploy):
    decision = recheck("b" * 40, compare_status)

    assert decision.deploy is deploy
    assert not decision.warn
    if not deploy:
        assert compare_status in decision.reason and "bbbbbbb" in decision.reason

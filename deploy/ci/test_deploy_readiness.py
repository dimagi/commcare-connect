import subprocess

import deploy_readiness
import pytest
from deploy_readiness import ReadinessDecision, _write_outputs, decide, latest_release_sha

RUN_URL = "https://github.com/dimagi/commcare-connect/actions/runs/1"


def ci_run(sha, status="completed", conclusion="success"):
    return {"headSha": sha, "status": status, "conclusion": conclusion, "url": RUN_URL}


@pytest.mark.parametrize(
    "head, release_status, run, deploy, warn, reason_part",
    [
        pytest.param("a" * 40, "identical", None, False, False, "nothing to deploy", id="nothing-new"),
        # The release has commits main doesn't, e.g. main was reset after a deploy
        pytest.param("a" * 40, "behind", None, False, True, "isn't in main's history", id="release-ahead-of-main"),
        pytest.param("b" * 40, "diverged", None, False, True, "isn't in main's history", id="diverged"),
        pytest.param("b" * 40, "ahead", ci_run("b" * 40), True, False, "", id="green"),
        pytest.param("b" * 40, "ahead", ci_run("b" * 40, conclusion="failure"), False, True, "failure", id="failed"),
        pytest.param(
            "b" * 40,
            "ahead",
            ci_run("b" * 40, status="in_progress", conclusion=None),
            False,
            True,
            "in_progress",
            id="in-progress",
        ),
        pytest.param("b" * 40, "ahead", None, False, True, "no CI run", id="no-run"),
        pytest.param("b" * 40, None, ci_run("b" * 40), True, False, "", id="no-release-yet"),
        # A passing run for an older commit doesn't vouch for main's head, whatever changed since
        pytest.param("c" * 40, "ahead", ci_run("b" * 40), False, True, "not been run", id="run-for-older-commit"),
    ],
)
def test_decide(head, release_status, run, deploy, warn, reason_part):
    decision = decide(head, release_status, run)

    assert isinstance(decision, ReadinessDecision)
    assert (decision.deploy, decision.warn) == (deploy, warn)
    assert reason_part in decision.reason


@pytest.mark.parametrize(
    "run",
    [
        pytest.param(ci_run("b" * 40, conclusion="failure"), id="failed"),
        pytest.param(ci_run("b" * 40, status="queued", conclusion=None), id="queued"),
        pytest.param(ci_run("a" * 40), id="run-for-older-commit"),
    ],
)
def test_warning_names_commit_and_run(run):
    decision = decide("b" * 40, "ahead", run)

    assert decision.warn
    assert "bbbbbbb" in decision.reason
    assert RUN_URL in decision.reason


def test_warning_without_run_names_commit():
    decision = decide("b" * 40, "ahead", None)

    assert "bbbbbbb" in decision.reason


def test_latest_release_sha_none_when_repo_has_no_release(monkeypatch):
    def gh(*args):
        raise subprocess.CalledProcessError(1, ["gh", *args], stderr="gh: Not Found (HTTP 404)")

    monkeypatch.setattr(deploy_readiness, "_gh", gh)

    assert latest_release_sha("dimagi/commcare-connect") is None


def test_latest_release_sha_raises_on_other_errors(monkeypatch):
    def gh(*args):
        raise subprocess.CalledProcessError(1, ["gh", *args], stderr="gh: API rate limit exceeded (HTTP 403)")

    monkeypatch.setattr(deploy_readiness, "_gh", gh)

    with pytest.raises(subprocess.CalledProcessError):
        latest_release_sha("dimagi/commcare-connect")


def test_readiness_skips_quietly_without_checking_ci_when_main_is_released(monkeypatch):
    def gh(*args):
        if args[:2] == ("api", "repos/dimagi/commcare-connect/commits/main"):
            return "a" * 40
        raise subprocess.CalledProcessError(1, ["gh", *args], stderr="gh: API rate limit exceeded (HTTP 403)")

    monkeypatch.setattr(deploy_readiness, "_gh", gh)
    monkeypatch.setattr(deploy_readiness, "_release_status", lambda repo, sha: "identical")

    decision, sha = deploy_readiness._check_readiness("dimagi/commcare-connect")

    assert (decision.deploy, decision.warn, sha) == (False, False, "a" * 40)


def test_write_outputs_keeps_a_multiline_reason_on_one_line(monkeypatch, tmp_path):
    output = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))

    _write_outputs(ReadinessDecision(deploy=False, warn=True, reason="CI failed\ndeploy=true"), "abc1234")

    assert output.read_text().splitlines() == [
        "deploy=false",
        "warn=true",
        "reason=CI failed deploy=true",
        "sha=abc1234",
    ]

import pytest
from scheduled_deploy_gate import GateDecision, decide

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

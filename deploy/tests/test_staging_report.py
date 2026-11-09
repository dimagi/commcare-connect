import pytest

from deploy import staging_report as report
from deploy.tests.git_helpers import add_commit_to, branch, commit, push

LEAF_MIGRATION_A = "commcare_connect/opportunity/migrations/0152_a.py"
LEAF_MIGRATION_B = "commcare_connect/opportunity/migrations/0152_b.py"
LEAF_ERROR = (
    "CommandError: Conflicting migrations detected; multiple leaf nodes in the migration graph: "
    "(0152_a, 0152_b in opportunity).\nTo fix them run 'python manage.py makemigrations --merge'"
)


class TestSuspectBranches:
    def test_build_names_merge_failures_and_both_sides_of_a_conflict(self, repo):
        output = (
            "  [.] origin/c NOT FOUND\n"
            "[.] origin/a => autostaging\n"
            "origin/a conflicts with origin/b\n"
            "origin/a conflicts with main\n"
        )
        found = report.suspect_branches(repo=repo, step="build", output=output, branches=["b", "x", "a", "c"])
        assert found == ["b", "a", "c"]

    def test_migration_conflict_names_branches_that_add_the_leaf_migrations(self, repo):
        branch(repo, name="a", files=[LEAF_MIGRATION_A])
        branch(repo, name="b", files=[LEAF_MIGRATION_B])
        branch(repo, name="other")
        found = report.suspect_branches(repo=repo, step="migrations", output=LEAF_ERROR, branches=["a", "other", "b"])
        assert found == ["a", "b"]

    def test_leaf_migration_already_on_main_is_not_blamed_on_branches(self, repo):
        commit(repo, filename=LEAF_MIGRATION_A)
        push(repo, "main")
        branch(repo, name="unrelated")
        branch(repo, name="b", files=[LEAF_MIGRATION_B])
        found = report.suspect_branches(repo=repo, step="migrations", output=LEAF_ERROR, branches=["unrelated", "b"])
        assert found == ["b"]

    @pytest.mark.parametrize(
        "step, output",
        [
            ("migrations", "Migrations for 'opportunity':\n  commcare_connect/opportunity/migrations/0153_x.py"),
            ("check", "opportunity.Visit.user: (fields.E300) Field defines a relation with model 'Nope'"),
        ],
    )
    def test_app_errors_name_branches_that_change_the_app(self, repo, step, output):
        branch(repo, name="changes-app", files=["commcare_connect/opportunity/models.py"])
        branch(repo, name="changes-other-app", files=["commcare_connect/users/models.py"])
        found = report.suspect_branches(
            repo=repo, step=step, output=output, branches=["changes-other-app", "changes-app"]
        )
        assert found == ["changes-app"]

    def test_deleted_branch_is_skipped(self, repo):
        found = report.suspect_branches(repo=repo, step="migrations", output=LEAF_ERROR, branches=["gone"])
        assert found == []


def test_leaf_migration_paths_across_apps():
    output = "multiple leaf nodes in the migration graph: (0002_a, 0002_b in users; 0152_c, 0152_d in opportunity)."
    assert report.leaf_migration_paths(output) == [
        "commcare_connect/users/migrations/0002_a.py",
        "commcare_connect/users/migrations/0002_b.py",
        "commcare_connect/opportunity/migrations/0152_c.py",
        "commcare_connect/opportunity/migrations/0152_d.py",
    ]


class TestDescribeSuspects:
    CONFIG = "branches:\n  - main\n  - a  # Charl Oct 6\n  - gone # Pawan Nov 7\n"

    def test_owner_comes_from_the_config_and_slack_id_from_the_author_email(self, repo, monkeypatch, httpx_mock):
        monkeypatch.setenv("GIT_AUTHOR_EMAIL", "charl@example.com")
        branch(repo, name="a")
        httpx_mock.add_response(
            url=f"{report.SLACK_LOOKUP_URL}?email=charl%40example.com", json={"ok": True, "user": {"id": "U123"}}
        )

        suspects = report.describe_suspects(repo=repo, names=["a"], config_text=self.CONFIG, slack_token="token")

        assert suspects == [report.Suspect(branch="a", owner="Charl", slack_id="U123")]
        assert suspects[0].mention() == "<@U123>"

    def test_author_is_whoever_made_the_first_commit_not_on_main(self, repo, monkeypatch):
        monkeypatch.setenv("GIT_AUTHOR_EMAIL", "main-author@example.com")
        commit(repo, filename="on_main.txt")
        push(repo, "main")
        monkeypatch.setenv("GIT_AUTHOR_EMAIL", "creator@example.com")
        branch(repo, name="a")
        monkeypatch.setenv("GIT_AUTHOR_EMAIL", "later@example.com")
        add_commit_to(repo, name="a", filename="later.txt")

        assert report.author_email(repo=repo, branch="a") == "creator@example.com"

    def test_falls_back_to_the_owner_name_without_a_slack_token(self, repo):
        suspects = report.describe_suspects(repo=repo, names=["gone"], config_text=self.CONFIG, slack_token="")
        assert suspects[0].mention() == "Pawan"


@pytest.mark.parametrize(
    "response",
    [{"json": {"ok": False, "error": "missing_scope"}}, {"json": {"ok": False, "error": "users_not_found"}}],
)
def test_slack_lookup_failure_returns_no_id(httpx_mock, response):
    httpx_mock.add_response(**response)
    assert report.slack_user_id(email="someone@example.com", token="token") == ""


def test_slack_lookup_network_error_returns_no_id(httpx_mock):
    httpx_mock.add_exception(report.httpx.ConnectError("down"))
    assert report.slack_user_id(email="someone@example.com", token="token") == ""


@pytest.mark.parametrize(
    "step, output, expected",
    [
        ("migrations", f"System check identified some issues:\nWARNINGS:\nx: (fields.W342)\n{LEAF_ERROR}", LEAF_ERROR),
        (
            "check",
            "ERRORS:\nopportunity.Visit.user: (fields.E300) Bad\n\tHINT: x\nWARNINGS:\ny: (fields.W340) z",
            "opportunity.Visit.user: (fields.E300) Bad",
        ),
        ("build", "[.] origin/a => autostaging\n[.] origin/a => autostaging\nok line", "[.] origin/a => autostaging"),
    ],
)
def test_failure_details(step, output, expected):
    assert report.failure_details(step=step, output=output) == expected


def test_slack_payload_lists_suspects():
    suspects = [report.Suspect(branch="a", owner="Charl", slack_id="U1"), report.Suspect("b", "", "")]
    payload = report.slack_payload(step="migrations", details="boom", suspects=suspects, run_url="https://run")
    texts = [block.get("text", {}).get("text", "") for block in payload["blocks"]]
    assert "*Likely cause:*\n• `a`: <@U1>\n• `b`: owner unknown" in texts
    assert "```boom```" in texts


def test_other_failures_name_no_branches(repo):
    assert report.suspect_branches(repo=repo, step="other", output="", branches=["a"]) == []
    assert report.failure_details(step="other", output="anything") == ""


def test_failure_details_fit_in_a_slack_block():
    output = "\n".join(f"app.Model{i}: (fields.E300) {'x' * 300}" for i in range(20))
    assert len(report.failure_details(step="check", output=output)) == report.MAX_DETAIL_CHARS

import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("worktree", Path(__file__).parent / "worktree.py")
worktree = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worktree)


@pytest.mark.parametrize(
    "branch, expected",
    [
        ("feature_x", "connect_wt_feature_x_38f2695f"),
        ("cs/Feature-X", "connect_wt_cs_feature_x_ac9df7e7"),
        # Would collide with cs/Feature-X if only changed names got a hash.
        ("cs_feature_x_ac9df7e7", "connect_wt_cs_feature_x_ac9df7e7_410b49bb"),
    ],
)
def test_database_name(branch, expected):
    assert worktree.database_name(branch) == expected


def test_database_name_fits_postgres_limit_for_long_branches():
    name = worktree.database_name("a" * 100)

    assert len(f"test_{name}_gw99") == 63
    assert name != worktree.database_name("a" * 101)


@pytest.mark.parametrize(
    "current, used, expected",
    [
        (0, {0}, 1),  # fresh worktree: .env copied from the main checkout
        (3, {0, 1}, 3),  # already set up
        (1, {0, 1}, 2),  # another worktree has the same database
        (15, {0}, 1),  # the test suite's database is never handed out
    ],
)
def test_choose_redis_database(current, used, expected):
    assert worktree.choose_redis_database(current=current, used=used) == expected


def test_choose_redis_database_fails_when_all_are_used():
    with pytest.raises(SystemExit):
        worktree.choose_redis_database(current=0, used=set(range(15)))


@pytest.mark.parametrize(
    "url, expected",
    [
        (None, 0),
        ("redis://localhost:6379", 0),
        ("redis://localhost:6379/4", 4),
    ],
)
def test_redis_database_number(url, expected):
    assert worktree.redis_database_number(url) == expected


def test_env_file_editing(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text('DATABASE_URL="old"\nSECONDARY_DATABASE_URL=x\n')

    assert worktree.read_env_value(env_file, "DATABASE_URL") == "old"
    worktree.set_env_value(env_file, "DATABASE_URL", "new")
    worktree.set_env_value(env_file, "REDIS_URL", "r")
    worktree.comment_out_env_value(env_file, "SECONDARY_DATABASE_URL")

    assert env_file.read_text() == "DATABASE_URL=new\n# SECONDARY_DATABASE_URL=x\nREDIS_URL=r\n"


def test_setup_shares_main_checkout_database_and_redis(tmp_path, monkeypatch):
    main, branch_worktree = tmp_path / "main", tmp_path / "branch"
    main.mkdir()
    branch_worktree.mkdir()
    (main / ".env").write_text(
        "DATABASE_URL=postgres://localhost/commcare_connect\nREDIS_URL=redis://localhost:6379/0\n"
    )
    (branch_worktree / ".env").write_text(
        "DATABASE_URL=postgres://localhost/connect_wt_old\nSECONDARY_DATABASE_URL=x\n"
        "REDIS_URL=redis://localhost:6379/3\nCELERY_BROKER_URL=redis://localhost:6379/3\n"
    )
    monkeypatch.setattr(worktree, "main_worktree", lambda: main)
    monkeypatch.setattr(worktree, "create_database", lambda database_url: pytest.fail("created a database"))
    monkeypatch.setattr(
        worktree, "choose_redis_database", lambda current, used: pytest.fail("picked a Redis database")
    )

    worktree.setup(worktree=branch_worktree, share=True)

    assert (branch_worktree / ".env").read_text() == (
        "DATABASE_URL=postgres://localhost/commcare_connect\nSECONDARY_DATABASE_URL=x\n"
        "REDIS_URL=redis://localhost:6379/0\n# CELERY_BROKER_URL=redis://localhost:6379/3\n"
    )

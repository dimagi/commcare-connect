"""Give a git worktree its own PostgreSQL database and Redis database.

    worktree.py setup             # in a worktree: create its databases, point .env at them
    worktree.py setup --share     # in a worktree: use the main checkout's database and Redis database
    worktree.py teardown BRANCH   # anywhere: drop the branch's databases

scripts/setup-worktree.sh runs `setup`; the post-remove hook in .config/wt.toml runs `teardown`.
"""

import argparse
import hashlib
import re
import subprocess
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg2
import redis

DATABASE_PREFIX = "connect_wt_"
# PostgreSQL's 63-character limit, less Django's "test_" prefix and pytest-xdist's "_gwNN" suffix.
MAX_DATABASE_NAME_LENGTH = 63 - len("test_") - len("_gw99")
# Database 0 is the default for the main checkout; 15 is the test suite's (TEST_REDIS_DB).
WORKTREE_REDIS_DATABASES = range(1, 15)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("setup").add_argument(
        "--share", action="store_true", help="use the main checkout's database and Redis database"
    )
    commands.add_parser("teardown").add_argument("branch")
    args = parser.parse_args()

    if args.command == "setup":
        setup(worktree=Path.cwd(), share=args.share)
    else:
        teardown(branch=args.branch, env_file=main_worktree() / ".env")


def setup(worktree, share):
    env_file = worktree / ".env"
    if share:
        for key in ["DATABASE_URL", "REDIS_URL", "CELERY_BROKER_URL"]:
            copy_main_env_value(env_file=env_file, key=key)
    else:
        setup_own_database(worktree=worktree, env_file=env_file)
        setup_redis_database(worktree=worktree, env_file=env_file)

    database = urlsplit(read_env_value(env_file, "DATABASE_URL")).path.lstrip("/")
    redis_database = redis_database_number(read_env_value(env_file, "REDIS_URL"))
    print(f"[connect] Database {database}, Redis database {redis_database}.")


def setup_own_database(worktree, env_file):
    branch = git("branch", "--show-current", cwd=worktree)
    if not branch:
        raise SystemExit("The worktree has no branch checked out, so its database can't be named.")

    database_url = with_path(read_env_value(env_file, "DATABASE_URL"), database_name(branch))
    create_database(database_url)
    set_env_value(env_file, "DATABASE_URL", database_url)
    # A copied SECONDARY_DATABASE_URL would point this worktree's migrations at the main checkout's replica.
    comment_out_env_value(env_file, "SECONDARY_DATABASE_URL")


def setup_redis_database(worktree, env_file):
    redis_database = choose_redis_database(
        current=redis_database_number(read_env_value(env_file, "REDIS_URL")),
        used=redis_databases_used_by_other_worktrees(worktree),
    )
    redis_url = f"redis://localhost:6379/{redis_database}"
    if redis_url != read_env_value(env_file, "REDIS_URL"):
        # Newly assigned, so clear anything a removed worktree left in it.
        redis.Redis.from_url(redis_url).flushdb()

    set_env_value(env_file, "REDIS_URL", redis_url)
    set_env_value(env_file, "CELERY_BROKER_URL", redis_url)


def teardown(branch, env_file):
    name = database_name(branch)
    server_url = with_path(read_env_value(env_file, "DATABASE_URL"), "postgres")
    with connect(server_url) as cursor:
        cursor.execute(
            "SELECT datname FROM pg_database WHERE datname = %s OR datname ~ %s",
            [name, f"^test_{name}(_gw[0-9]+)?$"],
        )
        for (database,) in cursor.fetchall():
            cursor.execute(f'DROP DATABASE "{database}" WITH (FORCE)')
            print(f"[connect] Dropped {database}.")


def database_name(branch):
    """A PostgreSQL-safe name for the branch. It ends in a hash of the branch name, so that two
    branches never get the same name."""
    digest = hashlib.sha1(branch.encode()).hexdigest()[:8]
    name = re.sub(r"[^a-z0-9_]+", "_", branch.lower()).strip("_")
    max_length = MAX_DATABASE_NAME_LENGTH - len(DATABASE_PREFIX) - len(digest) - 1
    return f"{DATABASE_PREFIX}{name[:max_length]}_{digest}"


def choose_redis_database(current, used):
    """Keep the worktree's current Redis database if no other worktree uses it, else take the lowest free one."""
    if current in WORKTREE_REDIS_DATABASES and current not in used:
        return current
    for candidate in WORKTREE_REDIS_DATABASES:
        if candidate not in used:
            return candidate
    raise SystemExit("Every Redis database from 1 to 14 is used by another worktree. Remove one first.")


def redis_databases_used_by_other_worktrees(worktree):
    used = set()
    for path in worktree_paths():
        if path != worktree:
            used.add(redis_database_number(read_env_value(path / ".env", "REDIS_URL")))
    return used


def redis_database_number(url):
    """The database number in a Redis URL. A missing URL or path means 0, as in config/settings/base.py."""
    match = re.match(r"rediss?://[^/]*/(\d+)", url or "")
    return int(match.group(1)) if match else 0


def create_database(database_url):
    name = urlsplit(database_url).path.lstrip("/")
    with connect(with_path(database_url, "postgres")) as cursor:
        cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", [name])
        if not cursor.fetchone():
            cursor.execute(f'CREATE DATABASE "{name}"')
    with connect(database_url) as cursor:
        cursor.execute("CREATE EXTENSION IF NOT EXISTS postgis")


def connect(database_url):
    # CREATE DATABASE and DROP DATABASE can't run inside a transaction.
    connection = psycopg2.connect(database_url)
    connection.autocommit = True
    return connection.cursor()


def with_path(url, path):
    if not url:
        raise SystemExit("DATABASE_URL is not set in .env. Create .env from .env_template.")
    return urlunsplit(urlsplit(url)._replace(path=f"/{path}"))


def read_env_value(env_file, key):
    """The last value given to a key in an env file, or None."""
    value = None
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith(f"{key}="):
                value = line.removeprefix(f"{key}=").strip("'\"")
    return value


def copy_main_env_value(env_file, key):
    """Give the worktree the main checkout's value for a key, or no value if the main checkout has none."""
    value = read_env_value(main_worktree() / ".env", key)
    if value is None:
        comment_out_env_value(env_file, key)
    else:
        set_env_value(env_file, key, value)


def set_env_value(env_file, key, value):
    lines = env_file.read_text().splitlines()
    new_line = f"{key}={value}"
    if any(line.startswith(f"{key}=") for line in lines):
        lines = [new_line if line.startswith(f"{key}=") else line for line in lines]
    else:
        lines.append(new_line)
    env_file.write_text("\n".join(lines) + "\n")


def comment_out_env_value(env_file, key):
    lines = env_file.read_text().splitlines()
    lines = [f"# {line}" if line.startswith(f"{key}=") else line for line in lines]
    env_file.write_text("\n".join(lines) + "\n")


def main_worktree():
    return worktree_paths()[0]


def worktree_paths():
    output = git("worktree", "list", "--porcelain", cwd=Path.cwd())
    return [Path(line.removeprefix("worktree ")) for line in output.splitlines() if line.startswith("worktree ")]


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


if __name__ == "__main__":
    main()

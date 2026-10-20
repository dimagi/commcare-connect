"""Useful tasks for use when developing CommCare Connect.

This uses the `Invoke` library."""

import shlex
import shutil
import sys
from pathlib import Path

from invoke import Context, Exit, call, task

PROJECT_DIR = Path(__file__).parent


@task
def docker(c: Context, command):
    """Run docker compose"""
    if command == "up":
        c.run("docker compose -f docker-compose.yml up -d")
    elif command == "down":
        c.run("docker compose -f docker-compose.yml down")
    else:
        raise Exit(f"Unknown docker command: {command}", -1)


@task(pre=[call(docker, command="up")])
def up(c: Context):
    """Run docker compose [up]"""
    pass


@task(pre=[call(docker, command="down")])
def down(c: Context):
    """Run docker compose [down]"""
    pass


@task
def translations(c: Context):
    """Make Django translations"""
    c.run("python manage.py makemessages --all --ignore node_modules --ignore venv")
    c.run("python manage.py makemessages -d djangojs --all --ignore node_modules --ignore venv")
    c.run("python manage.py compilemessages")


@task
def build_js(c: Context, watch=False, prod=False):
    """Build the JavaScript and CSS assets"""
    if prod:
        if watch:
            print("[warn] Prod build can't be watched")
        c.run("npm run build")
    else:
        extra = "-watch" if watch else ""
        c.run(f"npm run dev{extra}")


@task(
    help={
        "base": "Branch to diff against (default: origin/$GITHUB_BASE_REF if set, else origin/main)",
        "similarity": "Minimum percent similarity (default: 90)",
        "min_lines": "Minimum lines in a duplicated region (default: 5)",
        "json": "Print JSON instead of markdown",
        "path": "Directory to scan (default: commcare_connect)",
    }
)
def duplicates(c: Context, base=None, similarity=90, min_lines=5, json=False, path="commcare_connect"):
    """Report duplicate code that overlaps lines changed on this branch."""
    if not shutil.which("treepeat"):
        raise Exit("treepeat is not installed. Run `uv sync` to install it.", code=1)
    args = ["python", "-m", "scripts.duplicates.find_touched_duplicates"]
    if base:
        args += ["--base", base]
    args += ["--similarity", str(similarity), "--min-lines", str(min_lines)]
    args += ["--format", "json" if json else "markdown", path]
    c.run(shlex.join(args), pty=sys.stdout.isatty())


@task
def setup_ec2(c: Context, env="staging", verbose=False, diff=False):
    run_ansible(c, env=env, verbose=verbose, diff=diff)

    kamal_cmd = f"kamal env push -d {env}"
    if verbose:
        kamal_cmd += " -v"
    with c.cd(PROJECT_DIR / "deploy"):
        c.run(kamal_cmd)


@task
def django_settings(c: Context, env="staging", verbose=False, diff=False):
    """Update the Django settings file on prod servers"""
    run_ansible(c, env=env, tags="django_settings", verbose=verbose, diff=diff, user="connect", become=False)
    print("\nSettings updated. A re-deploy is required to have the services use the new settings.")
    val = input("Do you want to re-deploy the Django services? [y/N] ")
    if val.lower() == "y":
        deploy(c, env=env)


@task
def restart_django(c: Context, env="staging", verbose=False, diff=False):
    """Restart the Django server on prod servers"""
    run_ansible(c, play="utils.yml", env=env, tags="restart", verbose=verbose, diff=diff)


@task
def run_ansible(
    c: Context,
    play="play.yml",
    env="staging",
    tags=None,
    verbose=False,
    diff=False,
    user="ubuntu",
    become=True,
    limit=None,
    check=False,
):
    ansible_cmd = f"ansible-playbook {play} -i {env}.inventory.yml"
    if tags:
        ansible_cmd += f" --tags {tags}"
    if verbose:
        ansible_cmd += " -v"
    if diff:
        ansible_cmd += " -D"
    if user:
        ansible_cmd += f" -u {user}"
    if become:
        ansible_cmd += " -b"
    if limit:
        ansible_cmd += f" --limit {limit}"
    if check:
        ansible_cmd += " --check"

    with c.cd(PROJECT_DIR / "deploy"):
        c.run(ansible_cmd)


@task
def deploy(c: Context, env="staging"):
    """Deploy the app to prod servers"""
    with c.cd(PROJECT_DIR / "deploy"):
        c.run(f"kamal deploy -d {env}")

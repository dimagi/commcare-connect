"""Useful tasks for use when developing CommCare Connect.

This uses the `Invoke` library."""

from pathlib import Path

from invoke import Context, Exit, call, task

from deploy import staging_add as staging

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


@task(
    help={
        "branch": "Branch to add (default: the current branch)",
        "yes": "Don't ask for confirmation",
        "skip_trial": "Don't run a trial build to check for conflicts first",
    }
)
def staging_add(c: Context, branch="", yes=False, skip_trial=False):
    """Add a branch to Connect staging. Compatibility branches go in the right place automatically."""
    try:
        _staging_add(branch=branch, yes=yes, skip_trial=skip_trial)
    except staging.StagingAddError as error:
        raise Exit(str(error), code=1)


def _staging_add(*, branch, yes, skip_trial):
    repo = PROJECT_DIR
    branch = branch or staging.current_branch(repo)
    if not branch:
        raise Exit("Not on a branch. Check one out or pass --branch.", code=1)
    if branch == staging.TRUNK:
        raise Exit(f"'{staging.TRUNK}' is always on staging.", code=1)

    print("Fetching origin...")
    staging.fetch_origin(repo)
    if not staging.ref_exists(repo=repo, ref=staging.origin_ref(branch)):
        raise Exit(f"'{branch}' isn't on origin. Push it first.", code=1)
    if unpushed := staging.unpushed_commit_count(repo=repo, branch=branch):
        raise Exit(f"'{branch}' has {unpushed} commit(s) that aren't on origin. Push them first.", code=1)

    config = staging.fetch_config()
    if branch in config.branches:
        print(f"'{branch}' is already on staging.")
        return
    if branch in config.commented_branches:
        raise Exit(
            f"'{branch}' is commented out in {staging.CONFIG_FILE}. Uncomment it there instead:\n"
            f"  https://github.com/{staging.CONFIG_REPO}/edit/main/{staging.CONFIG_FILE}",
            code=1,
        )

    before = staging.find_insert_before(
        repo=repo, branches=config.branches, new_branch=branch, pr_bases=staging.open_pr_bases()
    )
    if not skip_trial:
        _check_trial_build(repo=repo, branches=config.branches, new_branch=branch, before=before)

    position = f"before '{before}'" if before else "at the end"
    if not yes and input(f"Add '{branch}' {position}? [y/N] ").strip().lower() != "y":
        raise Exit("Not added.", code=1)

    run_id = staging.start_add_workflow(branch=branch, before=before)
    print(f"Running https://github.com/{staging.CONFIG_REPO}/actions/runs/{run_id}")
    if not staging.watch_run(run_id):
        raise Exit("Adding the branch failed. See the run above.", code=1)
    print(
        f"Added '{branch}' {position}. Staging will rebuild and deploy:\n"
        f"  https://github.com/{staging.CODE_REPO}/actions/workflows/rebuild-staging.yml"
    )


def _check_trial_build(*, repo, branches, new_branch, before):
    print("Running trial builds with and without the branch (this takes a moment)...")
    new_branches = staging.branches_with(branches=branches, new_branch=new_branch, before=before)
    added_failures, result = staging.trial_added_failures(repo=repo, branches=branches, new_branches=new_branches)
    if result.failed_unexpectedly:
        raise Exit(f"The trial build failed:\n{result.output}\nRerun with --skip-trial to add anyway.", code=1)
    if added_failures:
        raise Exit(
            f"Adding '{new_branch}' breaks the staging build at: {', '.join(sorted(added_failures))}\n\n"
            f"{staging.failure_details(result.output)}\n\n"
            "Fix the conflict, for example with a compatibility branch, and try again.",
            code=1,
        )
    if result.failures:
        print(f"Staging is already broken at: {', '.join(sorted(result.failures))}. This branch adds no new problems.")
    else:
        print("Trial build succeeded.")

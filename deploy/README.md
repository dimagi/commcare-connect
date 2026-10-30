# CommCare Connect Deployment

This folder contains the configuration and scripts for deploying CommCare Connect.

## Overview

CommCare Connect is deployed to AWS using Docker containers. The deployment is managed using [Kamal](https://kamal-deploy.org/), a Ruby-based deployment tool.
See https://semaphoreci.com/blog/mrsk.

Deploying commcare-connect uses the following tools:

- [Ansible](https://www.ansible.com/)

  - Setup of EC2 instances
  - Management of Docker container ENV files

- [Kamal](https://kamal-deploy.org/)

  - Deployment of Docker containers

- [1Password CLI](https://developer.1password.com/docs/cli/get-started/)

  - Some secrets are stored in 1Password and retrieved using the CLI

- [AWS CLI](https://aws.amazon.com/cli/)
  - Used to manage AWS resources

## Setup

### Kamal

(requires Ruby)

```bash
gem install kamal -v '~> 1.9.2'
```

### Ansible

This is only required if you need to update Django settings.

```bash
python3 -m pip install --user pipx
pipx install ansible
```

### 1Password CLI

See https://developer.1password.com/docs/cli/get-started/

Note: Do not use Flatpack or snap to install 1password CLI as these do not work with the SSH agent.

You will also need to update the 1Password configuration to allow it to access the SSH key:

_~/.config/1Password/ssh/agent.toml_

```toml
[[ssh-keys]]
vault = "Connect Tech"
```

See https://developer.1password.com/docs/ssh/agent for more details.

#### AWS CLI

```bash
aws configure sso --profile commcare-connect
aws sso login --profile commcare-connect
```

Note: If you used a different profile name you will need to set the `AWS_PROFILE` environment variable to the profile name.

The servers are reached over SSH through AWS Systems Manager, using their EC2 instance IDs (see the inventory
files), so you also need the
[Session Manager plugin](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html)
for the AWS CLI. Kamal and Ansible both set up the proxy themselves.

To test that SSH, 1Password and AWS are all working you can run (instance ID from `staging.inventory.yml`):

```bash
ssh -o ProxyCommand="aws ssm start-session --target %h --document-name AWS-StartSSHSession --parameters portNumber=%p" connect@i-08bbcc89fcb90b8c7
```

## Updating Django Settings

The Django settings are configured using the `deploy/roles/connect/templates/connect.docker.env.j2` file. The plain text
settings values are in `deploy/roles/connect/defaults/main.yml`, overridden per environment in the inventory files.
Secrets are stored in 1Password under the `Ansible Secrets - Staging` and `Ansible Secrets - Production` entries.

To update the Django settings:

```bash
inv django-settings
```

## Deploy

Ideally deploy should be done via GitHub actions however it can be run locally as follows:

```bash
inv deploy
```

### Scheduled deploys

The `Scheduled production deploy` workflow (`.github/workflows/scheduled-deploy.yml`) deploys `main` to production
every Monday, Wednesday and Friday at 12:00 UTC. GitHub sometimes starts scheduled runs a few minutes late. Before
deploying, it checks `main`:

- **Nothing new since the latest release:** the run skips quietly.
- **CI on `main` failed, is still running, or hasn't run:** the run skips the deploy and posts a warning to the
  production deploy channel. It doesn't retry, and the next slot tries again. Commits that only touch `docs/` don't
  need a CI run of their own.
- **The check itself fails** (for example a GitHub API error): the run skips and posts a warning.

The deploy is pinned to the commit that was checked. If a manual deploy is running, the scheduled one waits for it,
then checks again and skips quietly when production already has that commit or a newer one.

To stop scheduled deploys, go to **Actions → Scheduled production deploy**, open the **⋯** menu and choose
**Disable workflow**. Choose **Enable workflow** in the same place to resume them. Nothing reminds you to turn it back
on. Manual deploys through the `Deploy` workflow keep working while the schedule is disabled.

### Release notes

After every successful production deploy, scheduled or manual, the deploy starts `.github/workflows/release-notes.yml`
as a separate run. Claude writes the notes for the PRs in the commits between the previous release tag and the new
one, using the `connect-web-release-notes` skill from [dimagi-claude-workflows](https://github.com/dimagi/dimagi-claude-workflows).
It runs in a job of its own, with no publishing credentials. A second job then:

- prepends the notes to the [Web Release Notes](https://dimagi.atlassian.net/wiki/spaces/connectpublic/pages/3870556169/Web+Release+Notes)
  Confluence page (`deploy/ci/publish_confluence.py`),
- posts a summary to `#connect-product-feed`.

The Markdown is kept as a workflow artifact. Slack mentions such as `<!channel>` are stripped from the summary before
posting, so an edited PR body can't ping the channel.

Problems never affect the deploy run. The release-notes run fails, and a warning goes into the deploy's Slack thread
saying what failed. This covers the notes failing to generate or coming back incomplete, missing Confluence
credentials, or Confluence or Slack rejecting the post. When a release has nothing user-facing, Claude says so
explicitly and nothing is published. To retry, run **Actions → Release notes → Run workflow**, optionally with a
release tag. Choose **Publish to** to retry only the target that failed. Re-running is safe for Confluence: the page
is left alone when it already has that release. Slack has no such check and posts again.

Release-notes runs queue one at a time, so back-to-back deploys don't race on the Confluence page. GitHub keeps only
one waiting run, so if a third deploy finishes while one is waiting, run the replaced release by hand.

## Accessing logs

The logs from the Docker containers are shipped to CloudWatch. To access them you will need to use the AWS console.

You can also view them using Kamal:

```bash
kamal app logs
```

See `kamal app logs --help` for more details.

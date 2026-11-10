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

`HEALTH_CHECK_TOKENS` is set from the `health_check_tokens` secret, a list of tokens joined with commas; leaving the
secret out renders an empty value, which leaves the `/status/` health check pages open. See
`commcare_connect/health/README.md`.

To update the Django settings:

```bash
inv django-settings
```

## Deploy

Ideally deploy should be done via GitHub actions however it can be run locally as follows:

```bash
inv deploy
```

A deploy restarts the app containers only. Changes to Traefik's own arguments (`traefik.args` in
`config/deploy.yml`, such as the forwarded-header range it trusts) need the proxy restarted as well:

```bash
kamal traefik reboot -d <destination>
```

### Scheduled deploys

The `Scheduled production deploy` workflow (`.github/workflows/scheduled-deploy.yml`) deploys `main` to production
every Monday, Wednesday and Friday at 12:00 UTC. GitHub sometimes starts scheduled runs a few minutes late. It takes
the same concurrency group as manual production deploys, so it waits for any deploy already running and none can start
until it finishes. A group holds one waiting run at most: if a manual production deploy is waiting at 12:00, the
scheduled run replaces it and the manual one is cancelled, and a manual deploy started while the scheduled run waits
cancels the scheduled one. Once it has the group, it checks `main` against the latest release and CI:

- **Nothing new since the latest release:** the run skips and posts an info message to the production deploy
  channel.
- **The latest release isn't in `main`'s history** (it has commits `main` doesn't, for example after `main` was reset):
  the run skips and posts a warning, so someone can decide whether to deploy by hand.
- **CI on `main` failed, is still running, or hasn't run:** the run skips the deploy and posts a warning to the
  production deploy channel. It doesn't retry, and the next slot tries again.
- **The check itself fails** (for example a GitHub API error): the run skips and posts a warning.

The deploy is pinned to the commit that was checked, so anything merged while it runs waits for the next slot. To
retry a failed scheduled deploy, start a manual deploy or use **Re-run all jobs**. **Re-run failed jobs** reuses the
old commit without checking it again, so the deploy refuses to run when production already has something newer.

Every scheduled run posts to the production deploy channel. The deploy's own Slack messages show `(automatic)` for
these runs, and `(scheduled)` or `(adhoc)` for manual deploys. A slot with no message means the run never started (the
workflow is disabled, or GitHub dropped the schedule), it was cancelled while waiting for another deploy, or it
couldn't reach Slack.

To stop scheduled deploys, go to **Actions → Scheduled production deploy**, open the **⋯** menu and choose
**Disable workflow**. Choose **Enable workflow** in the same place to resume them. Nothing reminds you to turn it back
on. Manual deploys through the `Deploy` workflow keep working while the schedule is disabled.

## Accessing logs

The logs from the Docker containers are shipped to CloudWatch. To access them you will need to use the AWS console.

You can also view them using Kamal:

```bash
kamal app logs
```

See `kamal app logs --help` for more details.

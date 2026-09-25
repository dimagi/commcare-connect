# Staging → Terraform Migration Plan

## TL;DR

Staging isn't in eu-west-1 yet — Production already is (migrated by hand). Use that gap:
prove Terraform modules by **importing** Staging's current us-east-1 resources (`plan` until
clean, never `apply` against them), then use those same modules to **build Staging fresh**
in eu-west-1, wire up Ansible/Kamal, cut over, and destroy the old us-east-1 resources.
Production import is a separate, later exercise (Phase 5) using the now-proven modules.

| Phase | What happens |
|---|---|
| 0 | Repo layout, S3 state backend, CI lint checks |
| 1 | Inventory + `import` Staging's *existing* us-east-1 resources → clean `plan` proves the modules |
| 2 | New `terraform.tfvars` for eu-west-1 → `apply` creates brand-new resources |
| 3 | Ansible + Kamal configure the new instances, smoke test |
| 4 | Cutover DNS, `terraform destroy` the old us-east-1 resources |
| 5 | *(later)* Same modules, `import` Production's existing eu-west-1 resources |

One rule that runs through everything: no secrets in `tfvars`/state — use RDS's native
managed master password + the existing 1Password vault, not a new secrets store.

---

**Goal:** bring Staging under Terraform, proving the modules by importing them against the
*existing* us-east-1 resources, then use those same modules to build Staging fresh in
eu-west-1 — before Production is touched at all.

**Prereqs**
- Terraform CLI ≥ 1.10 (native S3 state locking via `use_lockfile` — DynamoDB locking is
  deprecated, don't set up a lock table)
- AWS CLI with the `commcare-connect` SSO profile: `aws sso login --profile commcare-connect`
- [Terraform AWS provider docs](https://registry.terraform.io/providers/hashicorp/aws/latest/docs) — you'll be here a lot

**Secrets — one rule throughout this plan:** never declare a `db_password`-style variable and
pass it via `tfvars`. Anything in a variable ends up in the state file in plaintext regardless
of `sensitive = true` (that flag only hides it from CLI output, it doesn't encrypt state). For
the one credential Terraform is forced to generate — the RDS master password — use
[`manage_master_user_password = true`](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/db_instance#manage_master_user_password)
on `aws_db_instance`: AWS generates and rotates it via Secrets Manager natively, no
`random_password` resource needed. If Ansible/Kamal need to read it, push it into the
**existing 1Password vault** (via the [`onepassword` Terraform provider](https://registry.terraform.io/providers/1Password/onepassword/latest/docs))
rather than standing up Secrets Manager as a second secret store alongside 1Password — this repo
already has one, use it.

---

## Phase 0 — Project setup

1. Directory layout in the repo:
   ```
   terraform/
     modules/
       network/  security/  iam/  compute/  database/  cache/  storage/  load_balancer/  observability/
     envs/
       staging-us/   # temporary — import validation only, deleted after cutover
       staging-eu/
   ```
   [Module structure guide](https://developer.hashicorp.com/terraform/language/modules/develop/structure)

2. One-time state bucket: S3, versioning **on**, encryption on. Each env gets its own state
   **key** in that bucket — never share one state file across environments.
   [S3 backend + locking](https://developer.hashicorp.com/terraform/language/backend/s3)

   <details>
   <summary>Commands + backend config</summary>

   ```bash
   aws s3api create-bucket --bucket dimagi-connect-terraform-state --region us-east-1 \
     --create-bucket-configuration LocationConstraint=us-east-1
   aws s3api put-bucket-versioning --bucket dimagi-connect-terraform-state \
     --versioning-configuration Status=Enabled
   aws s3api put-bucket-encryption --bucket dimagi-connect-terraform-state \
     --server-side-encryption-configuration '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
   ```

   `envs/staging-us/backend.tf`:
   ```hcl
   terraform {
     backend "s3" {
       bucket       = "dimagi-connect-terraform-state"
       key          = "staging-us/terraform.tfstate"   # staging-eu uses "staging-eu/terraform.tfstate"
       region       = "us-east-1"
       use_lockfile = true
     }
   }
   ```
   </details>

3. `envs/staging-us/provider.tf` pointing at the SSO profile → `terraform init`.

   <details>
   <summary>Commands</summary>

   ```hcl
   provider "aws" {
     region  = var.region
     profile = "commcare-connect"
   }
   ```
   ```bash
   cd terraform/envs/staging-us
   terraform init
   ```
   </details>

**Done when:** `terraform init` succeeds, state file exists in S3.

4. Add a PR check (GitHub Actions) for everything under `terraform/`, before any real code
   lands — cheap to add now, and catches malformed HCL / bad provider refs / obvious
   misconfigurations before they merge.

   <details>
   <summary>Example workflow step</summary>

   ```yaml
   - run: terraform fmt -check -recursive terraform/
   - run: terraform -chdir=terraform/envs/staging-eu validate
   - uses: terraform-linters/setup-tflint@v4
   - run: tflint --recursive terraform/
   - uses: aquasecurity/tfsec-action@v1.0.3
     with:
       working_directory: terraform/
   ```
   </details>

---

## Phase 1 — Prove the modules: import current Staging (us-east-1)

This is the real test of module correctness, on a target you can't break further — it's a
box you're about to decommission anyway.

1. **Inventory every resource staging depends on, with its ID, before writing any Terraform.**
   Same exercise as the "Current inventory" section of the production migration doc, just
   smaller. Write the result down as a table (resource type → ID → notes) — this list *is*
   your checklist and the thing you diff `terraform plan` against. Cross-check against
   `deploy/staging.inventory.yml` too, in case anything there is stale or missing from AWS.

   <details>
   <summary>Commands</summary>

   ```bash
   # instance details — shows VPC, subnet, attached SGs, IAM instance profile, AMI
   aws ec2 describe-instances --instance-ids i-08bbcc89fcb90b8c7 \
     --query 'Reservations[0].Instances[0].{VPC:VpcId,Subnet:SubnetId,SGs:SecurityGroups,Profile:IamInstanceProfile,AMI:ImageId}'

   # the security group(s) from above — full rule set
   aws ec2 describe-security-groups --group-ids <sg-id-from-above>

   # the subnet from above — AZ, CIDR
   aws ec2 describe-subnets --subnet-ids <subnet-id-from-above>

   # route table associated with that subnet
   aws ec2 describe-route-tables --filters Name=association.subnet-id,Values=<subnet-id>

   # IAM role behind the instance profile
   aws iam get-instance-profile --instance-profile-name <profile-name-from-above>

   # does staging have its own RDS / ElastiCache, or share one? check both
   aws rds describe-db-instances --query 'DBInstances[].{ID:DBInstanceIdentifier,VPC:DBSubnetGroup.VpcId}'
   aws elasticache describe-cache-clusters --query 'CacheClusters[].{ID:CacheClusterId}'
   ```
   </details>

2. Write the module code, instantiate each module in `envs/staging-us/main.tf`.

3. Import each resource. Prefer [`import` blocks](https://developer.hashicorp.com/terraform/language/import)
   (TF 1.5+) over ad hoc CLI `terraform import` — a block shows up in `terraform plan` before
   it does anything, so you review before it touches state.

   <details>
   <summary>Example + shortcut</summary>

   ```hcl
   import {
     to = module.compute.aws_instance.web
     id = "i-08bbcc89fcb90b8c7"
   }
   ```
   Shortcut: [`terraform plan -generate-config-out`](https://developer.hashicorp.com/terraform/language/import/generating-configuration)
   writes the resource block *for* you from the live resource — edit rather than hand-write:
   ```bash
   terraform plan -generate-config-out=generated.tf
   ```
   **Treat `generated.tf` as a scratchpad, not final code.** It's flat, unparameterized,
   one-resource-per-block — copy what you need into the real `modules/*` files, then delete
   `generated.tf`. Don't let it linger in the repo or get committed as-is.
   </details>

4. `terraform plan` after each batch. Fix config until the plan is **empty**. Do **not** `apply`
   here — apply would try to reconcile any diff by actually changing the live, working box.
   `plan` is the entire point of this phase.

   If a live resource has a minor drift you can't (or don't want to) clean up right now without
   risking downtime — an extra manually-added SG rule, say — add a temporary
   `lifecycle { ignore_changes = [...] }` on that specific attribute rather than fighting it to a
   clean plan you don't actually mean. Come back and remove the exception once it's genuinely
   resolved, don't leave it as permanent cover for undocumented drift.

**Done when:** `terraform plan` on `envs/staging-us` shows zero changes.

---

## Phase 2 — Parameterize for eu-west-1, build fresh

1. `envs/staging-eu/` — same modules, new `terraform.tfvars`.

   <details>
   <summary>Example tfvars</summary>

   ```hcl
   region        = "eu-west-1"
   azs           = ["eu-west-1a", "eu-west-1b"]
   instance_type = "t3.large"
   ```
   </details>

2. Confirm the AMI resolves via [`data "aws_ami"`](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/data-sources/ami)
   (filtered by name pattern, never a hardcoded ID) — this exact mistake is what broke the
   production migration when the pinned AMI went stale.

   <details>
   <summary>Example data source</summary>

   ```hcl
   data "aws_ami" "ubuntu_2404" {
     most_recent = true
     owners      = ["099720109477"] # Canonical
     filter {
       name   = "name"
       values = ["ubuntu/images/hvm-ssd/ubuntu-noble-24.04-amd64-server-*"]
     }
   }
   ```
   </details>

3. `terraform plan` — review carefully, this genuinely creates new resources.
4. `terraform apply`.
5. Record the outputs: instance IDs, any DB/cache endpoints.

   <details>
   <summary>Commands</summary>

   ```bash
   cd terraform/envs/staging-eu
   terraform init
   terraform plan -out=staging-eu.plan
   terraform apply staging-eu.plan
   terraform output -json > outputs.json
   ```
   </details>

**Done when:** `apply` completes clean, outputs captured.

---

## Phase 3 — Ansible + Kamal against the new instances

1. **Tag every instance in the `compute` module** (`Environment`, `Role`, `Project`) and point
   Ansible at AWS directly via the `aws_ec2` dynamic inventory plugin, instead of hand-editing
   `deploy/staging.inventory.yml` with instance IDs going forward. Removes the copy-paste step
   entirely — new/replaced instances just show up.

   <details>
   <summary>Tags + inventory plugin config</summary>

   ```hcl
   # in the compute module, per instance
   tags = {
     Environment = var.environment   # "staging"
     Role        = "web"             # or "celery"
     Project     = "commcare-connect"
   }
   ```

   `deploy/inventory_aws_ec2.yml`:
   ```yaml
   plugin: aws_ec2
   regions:
     - eu-west-1
   filters:
     tag:Project: commcare-connect
     tag:Environment: staging
   keyed_groups:
     - key: tags.Role
       prefix: ''
       separator: ''
   ```
   [`aws_ec2` inventory plugin docs](https://docs.ansible.com/ansible/latest/collections/amazon/aws/aws_ec2_inventory.html)
   </details>

2. Run the Ansible play against staging using that inventory. Any failure (missing security-group
   rule, missing IAM permission, AMI package mismatch) means the **Terraform module** is
   incomplete — fix it, `apply` again, retry. Don't hand-patch the AWS console.

   <details>
   <summary>Commands</summary>

   ```bash
   cd deploy
   ansible-playbook -i inventory_aws_ec2.yml play.yml
   ```
   </details>

3. Kamal has no dynamic-inventory equivalent, so `deploy/config/deploy.staging.yml` →
   `servers.web.hosts` still needs updating — script it from the Terraform output instead of
   hand-typing IDs.

   <details>
   <summary>Example script</summary>

   ```bash
   cd terraform/envs/staging-eu
   terraform output -json web_instance_ids | jq -r '.[]'
   # feed that into a small script that patches servers.web.hosts in deploy.staging.yml
   ```
   </details>

4. Deploy via Kamal.

   <details>
   <summary>Command</summary>

   ```bash
   cd deploy
   kamal deploy -d staging
   ```
   </details>

5. Smoke test: OAuth login, form submission, Celery tasks + beat schedule, S3 upload/download,
   outbound email — same checklist the production migration used.

   <details>
   <summary>Commands</summary>

   ```bash
   curl https://<staging-alb-hostname>/serverup.txt --insecure

   # inside the celery container
   celery -A config.celery_app inspect active
   celery -A config.celery_app inspect reserved
   celery -A config.celery_app inspect scheduled
   ```
   ```python
   # inside the web/celery container, for beat schedule
   from django_celery_beat.models import PeriodicTask
   [print(t.name) for t in PeriodicTask.objects.filter(enabled=True)]
   ```
   </details>

**Done when:** staging-eu passes the same smoke-test checklist the production migration used.

---

## Phase 4 — Cutover

1. Lower DNS TTL ahead of time (Cloudflare dashboard, or API against the staging record).
2. Flip staging's DNS to the new eu-west-1 ALB.
3. Confirm traffic flowing, watch it for a day.
4. `terraform destroy` on `envs/staging-us` — clean, since it's fully state-tracked. Delete the
   `staging-us` env directory once destroyed.

   <details>
   <summary>Command</summary>

   ```bash
   cd terraform/envs/staging-us
   terraform destroy
   ```
   </details>

**Done when:** old us-east-1 staging resources are gone; only `staging-eu` remains.

---

## Phase 5 — Production (separate exercise, later)

Not covered here. Once staging-eu has run clean for a while: same modules,
`terraform import` / `-generate-config-out` against Production's existing eu-west-1
resources, `plan` until clean — same mechanics as Phase 1, for keeps this time.

---

## Reference index

| Topic | Link |
|---|---|
| AWS provider (all resources/data sources) | https://registry.terraform.io/providers/hashicorp/aws/latest/docs |
| Import blocks | https://developer.hashicorp.com/terraform/language/import |
| Generating config from existing resources | https://developer.hashicorp.com/terraform/language/import/generating-configuration |
| `terraform import` (CLI form) | https://developer.hashicorp.com/terraform/cli/import |
| S3 backend + native locking | https://developer.hashicorp.com/terraform/language/backend/s3 |
| Module structure | https://developer.hashicorp.com/terraform/language/modules/develop/structure |
| `aws_ami` data source | https://registry.terraform.io/providers/hashicorp/aws/latest/docs/data-sources/ami |
| `aws_db_instance` | https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/db_instance |
| `aws_db_parameter_group` | https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/db_parameter_group |
| `aws_elasticache_cluster` | https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/elasticache_cluster |
| `aws_lb` / target groups / listeners | https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/lb |
| Workspaces vs. directories per environment | https://developer.hashicorp.com/terraform/language/state/workspaces |
| `aws_db_instance` managed master password | https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/db_instance#manage_master_user_password |
| `onepassword` Terraform provider | https://registry.terraform.io/providers/1Password/onepassword/latest/docs |
| Ansible `aws_ec2` dynamic inventory plugin | https://docs.ansible.com/ansible/latest/collections/amazon/aws/aws_ec2_inventory.html |
| tflint | https://github.com/terraform-linters/tflint |
| tfsec | https://github.com/aquasecurity/tfsec |

# CommCare Connect — AWS Region Migration Plan

**Date:** 2026-07-08
**Author:** Cal Ellowitz (with Claude)
**Status:** Draft for review

## Summary

Migrate the **commcare-connect** production environment from `us-east-1`
to **`eu-west-1` (Ireland)**, dictated by a data-residency
requirement. The migration uses a **manual rebuild** of the target region and
a **snapshot/restore + DNS cutover** performed inside a scheduled maintenance
window (hours of acceptable downtime).

ConnectID is **out of scope** and remains in `us-east-1`.

## Goals & Constraints

- **Driver:** Data residency / compliance. The requirement is **forward-looking**
  — regulated data created *after* the migration must live in `eu-west-1` (Ireland).
  Residual data left in `us-east-1` (snapshots, old S3 objects, logs) is
  acceptable, so no destruction/attestation phase is required.
- **Downtime budget:** A scheduled maintenance window (hours). No live
  replication or blue-green needed; a snapshot/restore cutover is sufficient.
- **Provisioning model:** The underlying AWS resources were created **manually
  (ClickOps)**. The target region will be rebuilt manually and documented as a
  runbook. (Codifying as Terraform was considered and declined.)
- **Scope:** commcare-connect only (2 web + 1 celery EC2 instances and their
  backing services). ConnectID stays behind.

## Source Environment Inventory (`us-east-1`)

Derived from `deploy/` and `config/settings/base.py`. Phase 0 confirms exact
values.

| Component | Current state | Regional? | Migration handling |
|---|---|---|---|
| Compute | EC2: 2 web (`i-0901d8af0c29c69cf`, `i-0a766713cb0d2fe96`), 1 celery (`i-0816b5db3a153135e`), managed by Kamal + Ansible, reached via SSM | Yes | New instances, Ansible-bootstrapped, Kamal-deployed |
| Container registry | ECR `037129986032.dkr.ecr.us-east-1.amazonaws.com` | Yes | New ECR repo in target region; push image |
| Primary DB | RDS PostgreSQL + PostGIS (`RDS_HOSTNAME`) | Yes | Snapshot → cross-region copy → restore |
| Secondary DB | Separate DB via `SECONDARY_DATABASE_URL`, logical replication (`multidb` app + `db_router.py`) | Yes | Snapshot/restore both DBs at a quiesced, zero-lag point; re-point replication with `copy_data = false` (see Decisions) |
| Cache / broker | Redis (`REDIS_URL` = Django cache; `CELERY_BROKER_URL` + result backend) — likely ElastiCache | Yes | Fresh, empty instance — data is disposable; drain Celery queue before cutover |
| Object storage | S3 (`DJANGO_AWS_STORAGE_BUCKET_NAME`) | Yes | New bucket; `aws s3 sync` (warm-up + final incremental) |
| Logging | CloudWatch via `awslogs` driver, region hardcoded `us-east-1`; groups `commcare-connect-web`, `commcare-connect-celery` | Yes | New log groups in target region; update region refs |
| Email | Amazon SES via anymail | Yes | Verify domain + DKIM in target region; **request production access early** |
| Secrets | 1Password vault `Ansible Secrets - Production` | No | Staged copy for dry-run; update endpoints for cutover |
| Front door | Application Load Balancer (ALB) → Traefik on hosts | Yes (ALB) | New ALB in `eu-west-1`; DNS repoints to it at cutover |
| DNS | **External provider** (e.g. Cloudflare) — not Route 53 | No | Lower TTL ahead; flip record in the external provider at cutover |
| TLS | **ACM certificate on the ALB** | ACM is regional | Request + DNS-validate a new ACM cert in `eu-west-1` (Phase 1) |
| External integrations | CommCare HQ, ConnectID, Twilio, Mapbox, Sentry, Open Exchange Rates, GA/GTM | No | No move; verify allowlists / callback URLs |

## Repository Changes Required

Hardcoded `us-east-1` references to update (target-region cutover):

- `deploy/config/deploy.yml` — `logging.options.awslogs-region`, `registry.server`
- `deploy/config/deploy.production.yml` — `web`/`celery` host instance IDs
- `deploy/production.inventory.yml` — `ansible_host` instance IDs
- `deploy/roles/connect/defaults/main.yml` — `aws_region: us-east-1`
- `deploy/roles/connect/templates/cloudwatch-config.json.j2` — region references
- `registry_password.sh` — ECR region for `aws ecr get-login-password`
- 1Password secrets — `RDS_HOSTNAME`, `RDS_PORT`, `SECONDARY_DATABASE_URL`,
  `REDIS_URL`, `CELERY_BROKER_URL`, `DJANGO_AWS_STORAGE_BUCKET_NAME`,
  `AWS_DEFAULT_REGION`, and any `csrf_trusted_origins` / `django_allowed_hosts`
  changes if the hostname changes.

## Phases

### Phase 0 — Discovery & inventory (no changes)

Enumerate and document every `us-east-1` resource and its exact configuration —
this is both the build spec for the target region and the parity checklist:

- Security group rules (inbound/outbound, source refs)
- RDS: engine version, instance class, storage, parameter group, option group,
  PostGIS extension version, KMS key, automated-backup config, **DB size**
- Secondary DB: how logical replication is configured (publications,
  subscriptions, replication slots, replica identity)
- ElastiCache: node type, engine version, cluster mode
- S3: bucket policy, CORS, lifecycle rules, encryption, **object count/size**
- IAM instance profile + attached policies (SSM, ECR pull, CloudWatch, SES, S3)
- DNS records (app hostname, SES DKIM/SPF/DMARC/MAIL FROM) and their TTLs, in
  the external DNS provider
- ALB config: listeners, target groups, health checks, and the ACM cert
  attached to the HTTPS listener
- SES: sandbox vs production, verified identities, sending limits

**Measure** cross-region snapshot-copy duration and full S3 sync duration — these
determine whether the DB and storage steps fit the maintenance window.

**Output:** written resource inventory doc.

### Phase 1 — Build the target region (parallel to prod, zero impact)

Rebuild manually, documenting each step as a runbook:

1. VPC, subnets (multi-AZ), route tables, IGW/NAT, security groups
2. IAM instance profile (IAM is global; ensure region-agnostic permissions)
3. ECR repository; build + push the app image
4. ElastiCache Redis (empty)
5. S3 bucket (mirror policy/CORS/lifecycle/encryption from Phase 0)
6. CloudWatch log groups (or rely on `awslogs` auto-create)
7. EC2 instances (web ×2, celery ×1) + SSM agent + instance profile; bootstrap
   with the existing Ansible `connect` role
8. **SES: verify domain + DKIM and request production access NOW** — AWS approval
   has a 24–48h lead time and is the critical-path long pole. Add the DKIM/SPF/
   MAIL FROM records in the external DNS provider (these can coexist with the
   `us-east-1` SES records without disruption)
9. ALB: create ALB + target groups + health checks; request a new ACM cert in
   `eu-west-1` and DNS-validate it by adding the ACM CNAME in the external DNS
   provider; attach the cert to the HTTPS listener
10. Staged copy of the 1Password secrets pointing at new (non-prod-traffic)
    endpoints for the dry run

### Phase 2 — Dry-run rehearsal

Rehearse the entire cutover against staging or a clone (not live prod):

- Restore a real snapshot into the target-region RDS
- Run `aws s3 sync` and confirm object parity
- Deploy the app via Kamal to the new instances
- Smoke-test end to end
- **Time every step** and refine the window plan
- Capture any undocumented ClickOps config that surfaces

Exit criterion: a clean dry run with a timed, step-by-step cutover runbook.

### Phase 3 — Pre-cutover (days ahead)

- Lower DNS TTL on the app hostname in the external DNS provider (e.g. to 60s)
  far enough ahead to propagate
- Warm-up `aws s3 sync` while prod is live (bulk copy; only a small incremental
  delta remains for the window)
- Freeze non-essential production deploys

### Phase 4 — Cutover (maintenance window)

1. Announce downtime; enable maintenance page
2. Stop web app (halt new writes)
3. **Drain Celery:** stop beat, let in-flight workers finish, confirm broker
   empty (queued tasks in Redis are lost otherwise; beat schedule survives in DB)
4. Final RDS snapshot → cross-region copy → restore in target region
   (handle KMS re-encryption with a target-region key)
5. Final incremental `aws s3 sync`
6. Snapshot the secondary at the same quiesced/zero-lag point, restore it in
   `eu-west-1`, and re-point logical replication with `copy_data = false`
   (see Decisions)
7. Repoint secrets/config at new endpoints; update repo region references
8. Deploy app via Kamal to target region; run **internal** smoke tests before
   exposing traffic
9. **Flip DNS** in the external provider to point the app hostname at the new
   `eu-west-1` ALB
10. End-to-end verification:
    - Login via ConnectID OAuth (now a **cross-region** call to `us-east-1`)
    - Form submission (form_receiver)
    - Payments workflow
    - Maps / microplanning (Mapbox)
    - File upload **and** download against the new S3 bucket
    - Outbound email via the new-region SES
    - Celery task execution + beat schedule intact
11. Lift maintenance page

### Phase 5 — Validation & rollback window

- Monitor Sentry, CloudWatch, error rates, latency (watch ConnectID
  cross-region latency specifically)
- Keep `us-east-1` **intact but idle** as a rollback target
- **Rollback** = flip DNS back + re-enable the old app. Valid only until the new
  region has accepted production writes that can't be reconciled back.
  **Point of no return:** lifting the maintenance page and accepting live writes.
  Keep the window tight and verify thoroughly before that point.

### (Out of scope) Old-region cleanup

Because compliance is forward-looking, tearing down `us-east-1` is **not
required**. It can be done later purely for cost savings, on its own schedule,
and is not part of this plan.

## Risks & Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| SES production-access approval lead time | Cannot send email post-cutover | Request in Phase 1, first thing |
| Cross-region snapshot-copy time exceeds window | Blown maintenance window | Measure in Phase 0; if too large, pre-seed via cross-region read replica and promote |
| Encrypted-snapshot KMS handling across regions | Restore fails | Copy snapshot with a target-region KMS key |
| Undocumented ClickOps config | Parity gaps, cutover failure | Phase 0 inventory + Phase 2 dry run |
| In-flight Celery tasks lost | Dropped background work | Drain queue before cutover (Phase 4 step 3) |
| DNS propagation delay | Extended perceived downtime | Lower TTL in Phase 3 |
| Hardcoded `us-east-1` in repo | App points at old region | Update all refs (see Repository Changes) |
| ConnectID cross-region latency | Slower OAuth/login | Accept; monitor in Phase 5 |

## Decisions

1. **Target region:** `eu-west-1` (Ireland).
2. **Front door:** Application Load Balancer. DNS points at the ALB; cutover
   repoints the app hostname to the new `eu-west-1` ALB.
3. **TLS:** ACM certificate on the ALB. ACM is regional, so a new cert is
   requested and DNS-validated in `eu-west-1` during Phase 1.
4. **DNS provider:** External (e.g. Cloudflare) — *not* Route 53. TTL lowering
   and the cutover flip happen in the external provider.
5. **Secondary DB:** **Snapshot/restore both DBs at a quiesced, zero-lag point,
   then re-point logical replication with `copy_data = false`.** Because the
   maintenance window halts all writes, both snapshots are taken at an identical,
   consistent point — so the expensive initial table copy is skipped and both
   DBs are immediately usable, while replication resumes for future writes.
   **Fallback:** if Phase 0 finds the secondary dataset is small or the
   replication setup is messier than expected, rebuild the secondary fresh with
   `copy_data = true` and re-initialize replication. Phase 0 data-size is the
   tiebreaker.
6. **ConnectID residency:** ConnectID is **confirmed outside** the data-residency
   requirement. It stays in `us-east-1` as an external dependency; commcare-connect
   will make cross-region OAuth introspection calls to it (accepted latency cost).
   No follow-up ConnectID migration is required.

## Open Questions

None outstanding. All decisions resolved.

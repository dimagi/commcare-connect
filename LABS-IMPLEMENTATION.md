# Labs AWS Fargate Deployment - Implementation Guide

## Quick Start

**To deploy via GitHub Actions:**

1. Go to Actions tab → "Deploy to AWS Labs"
2. Click "Run workflow"
3. Select branch and migration option
4. Click "Run workflow"

## Overview

This document describes the AWS Fargate deployment for the CommCare Connect labs environment. Labs is designed for throwaway prototypes with minimal maintenance overhead.

## Branching Strategy

**TL;DR:** `main` stays pure, `labs-main` adds deployment tooling, feature branches off `labs-main`.

### Branch Structure

- **`main`**: Pure mirror of `dimagi/commcare-connect` main branch

  - No labs-specific code
  - No deployment workflows
  - Kept clean for easy upstream sync
  - Never deploy from this branch

- **`labs-main`**: Fork's working branch for labs infrastructure

  - Contains `.github/workflows/deploy-labs.yml`
  - Contains `requirements/labs.txt` for prototype dependencies
  - Contains `LABS-IMPLEMENTATION.md`
  - Set as default branch in GitHub (for Actions UI)
  - Base branch for all feature work

- **Feature branches**: Created from `labs-main`
  - `jj/feature-name` naming convention
  - Inherit all deployment tooling from `labs-main`
  - Can deploy directly via GitHub Actions

### Workflow

```
dimagi/main → (sync) → jjackson/main → (merge) → jjackson/labs-main → (branch) → feature branches
                        (pure upstream)           (+ deployment tools)            (+ your changes)
```

**Sync from upstream:**

```bash
# Update main (pure mirror)
git checkout main
git pull origin main  # from dimagi/commcare-connect
git push fork main

# Merge into labs-main
git checkout labs-main
git merge main  # Bring in upstream changes
# Resolve any conflicts in base.txt/production.txt
git push fork labs-main
```

**Work on features:**

```bash
# Create feature branch from labs-main
git checkout labs-main
git checkout -b jj/my-feature

# Work and commit
# Deploy directly from feature branch via GitHub Actions

# When done, merge back to labs-main (or delete if throwaway)
```

### Why This Approach?

1. **Easy upstream sync**: `main` has zero divergence from dimagi
2. **Clean separation**: Deployment code isolated to `labs-main`
3. **No pollution**: Production repo never sees labs deployment tooling
4. **Flexible**: Feature branches can deploy independently
5. **Safe**: Can always reset `main` to match dimagi exactly

## Architecture

### High-Level Design

```
┌─────────────────────────────────────────────────────────────┐
│                         Internet                             │
└────────────────────────┬────────────────────────────────────┘
                         │
                       HTTPS
                         │
              ┌──────────▼──────────┐
              │  Application Load   │
              │  Balancer (ALB)     │
              │  labs.connect.      │
              │  dimagi.com          │
              └──────────┬──────────┘
                         │
                    Port 8000
                         │
┌────────────────────────▼────────────────────────────────────┐
│                    Public Subnets                            │
│  ┌──────────────────┐         ┌──────────────────┐         │
│  │  ECS Fargate     │         │  ECS Fargate     │         │
│  │  Web Task        │         │  Worker Task     │         │
│  │  (Gunicorn)      │         │  (Celery)        │         │
│  └────────┬─────────┘         └─────────┬────────┘         │
│           │                              │                   │
│           └──────────────┬───────────────┘                   │
│                          │                                   │
│           ┌──────────────▼───────────────┐                  │
│           │   Security Group             │                  │
│           │   - HTTP: 8000 (from ALB)    │                  │
│           │   - PostgreSQL: 5432         │                  │
│           │   - Redis: 6379              │                  │
│           └──────────────┬───────────────┘                  │
└────────────────────────────────────────────────────────────┘
                          │
          ┌───────────────┴───────────────┐
          │                               │
┌─────────▼────────┐          ┌──────────▼──────────┐
│  RDS PostgreSQL  │          │  ElastiCache Redis  │
│  db.t3.micro     │          │  cache.t3.micro     │
│  (managed)       │          │  (managed)          │
└──────────────────┘          └─────────────────────┘
```

### Key Design Decisions

1. **Application Load Balancer (ALB)**: Provides stable HTTPS endpoint at labs.connect.dimagi.com
2. **No NAT Gateway**: Tasks run in public subnets with direct internet access, saving ~$32/month
3. **Managed Databases**: RDS and ElastiCache handle OS patching, backups, and security updates
4. **Fargate Launch Type**: AWS manages the underlying compute, eliminating EC2 management
5. **Single Task Deployment**: Each service runs 1 task for minimal cost (suitable for labs environment)
6. **Dynamic Infrastructure Discovery**: GitHub Actions queries AWS for infrastructure IDs at runtime
7. **Secrets in Secrets Manager**: All sensitive data (DB password, secret key) stored securely

## Infrastructure

### VPC and Networking

- **VPC**: `labs-jj-vpc` (10.0.0.0/16, us-east-1)
- **Subnets** (Public):
  - `labs-jj-public-subnet-1`: 10.0.1.0/24 (us-east-1a)
  - `labs-jj-public-subnet-2`: 10.0.2.0/24 (us-east-1b)
- **Security Group**: `labs-jj-sg`
  - Inbound: HTTP (8000), PostgreSQL (5432), Redis (6379)
  - Outbound: All traffic

### Database and Cache

- **RDS PostgreSQL**: `labs-jj-postgres`

  - Instance: db.t3.micro
  - Engine: PostgreSQL 15.5
  - Storage: 20 GB gp2
  - Database: `commcare_connect`
  - Multi-AZ: Disabled
  - Backups: 1 day retention

- **ElastiCache Redis**: `labs-jj-redis`
  - Node: cache.t3.micro
  - Engine: Redis 7.x
  - Nodes: 1

### Container Services

- **ECR Repository**: `labs-jj-commcare-connect`
- **ECS Cluster**: `labs-jj-cluster` (Fargate)
- **Services**: 2 (web, worker)

**Web Service**: `labs-jj-web`

- CPU: 512 (0.5 vCPU)
- Memory: 1024 MB
- Command: `/start` (Gunicorn on port 8000)
- Logs: CloudWatch `/ecs/labs-jj-web`

**Worker Service**: `labs-jj-worker`

- CPU: 512 (0.5 vCPU)
- Memory: 1024 MB
- Command: `/start_celery` (Celery worker)
- Logs: CloudWatch `/ecs/labs-jj-worker`

### IAM Roles

**Task Execution Role**: `labs-jj-ecs-task-execution-role`

- Pulls container images from ECR
- Reads secrets from Secrets Manager
- Creates CloudWatch log groups

**Task Role**: `labs-jj-ecs-task-role`

- Used by application code (currently no policies)

**GitHub Actions Role**: `github-actions-labs-deploy`

- OIDC authentication from GitHub Actions
- Permissions:
  - ECR: Push/pull images
  - ECS: Deploy services, run tasks
  - EC2: Query VPC/subnet/security group info
  - Secrets Manager: Read secrets (for IAM PassRole)

### Secrets Management

All sensitive data is stored in AWS Secrets Manager:

- `labs-jj-django-secret-key`: Django SECRET_KEY
- `labs-jj-database-url`: Full PostgreSQL connection string (including password)

**Security Note**: Database password is NOT visible in ECS task definitions. It's injected at runtime from Secrets Manager.

## GitHub Actions Deployment

### Workflow: `.github/workflows/deploy-labs.yml`

The deployment workflow:

1. **Query Infrastructure**: Dynamically discovers VPC, subnet, and security group IDs by tag names
2. **Build Docker Image**: Multi-stage build with Python and Node.js
3. **Push to ECR**: Tags with `latest` and commit SHA
4. **Run Migrations** (optional): One-off Fargate task with `/migrate` command
5. **Deploy Services**: Updates web and worker services with new image
6. **Wait for Stability**: Ensures services reach steady state
7. **Deployment Summary**: Displays application URL (https://labs.connect.dimagi.com/)

### Authentication

GitHub Actions uses **OIDC** (OpenID Connect) to authenticate with AWS:

- No long-lived access keys
- Short-lived credentials per workflow run
- Trust policy allows `repo:jjackson/commcare-connect:ref:refs/heads/*` (any branch)

### Usage

**Deploy from any branch:**

```
Actions → Deploy to AWS Labs → Run workflow
```

**Options:**

- `run_migrations`: Check to run database migrations before deployment (default: false)

**Output:**

```
Deployment complete
URL: http://[PUBLIC_IP]:8000/
Branch: labs-main | Commit: 7f3a738
```

## Requirements Structure

Labs uses a layered requirements approach:

- `base.txt` + `production.txt`: Pure mirrors of `dimagi/commcare-connect` main branch
- `labs.txt`: Labs-specific dependencies (e.g., pandas for prototypes)

This allows:

- Easy sync from upstream (just merge `main` from dimagi)
- Clear isolation of prototype dependencies
- No impact on production requirements

**Compile labs requirements:**

```bash
cd requirements/
pip-compile labs.in --upgrade-package [package] -o labs.txt
```

## Cost Estimate

**Monthly AWS costs (approximate):**

- RDS PostgreSQL (db.t3.micro): $15
- ElastiCache Redis (cache.t3.micro): $12
- ECS Fargate (2 tasks @ 0.5 vCPU, 1 GB): $16
- ECR storage: <$1
- Data transfer: ~$5
- **Total: ~$50/month**

**Cost savings vs traditional deployment:**

- No ALB: -$16/month
- No NAT Gateway: -$32/month
- Single availability zone: -50% on RDS/ElastiCache

## Troubleshooting

### Deployment Failures

**Check GitHub Actions logs:**

```
Actions → Deploy to AWS Labs → [workflow run] → Deploy to Fargate
```

**Common issues:**

- Build failures: Check Docker build step for dependency issues
- Migration failures: Check CloudWatch logs at `/ecs/labs-jj-web`
- Deployment timeout: Services may take 2-3 minutes to stabilize

### Application Issues

**View logs:**

```bash
aws logs tail /ecs/labs-jj-web --follow --profile labs
aws logs tail /ecs/labs-jj-worker --follow --profile labs
```

**Or via AWS Console:**

- CloudWatch → Log groups → `/ecs/labs-jj-web` or `/ecs/labs-jj-worker`

### Rollback

If a deployment breaks the application:

```bash
# Find previous working task definition
aws ecs list-task-definitions --family-prefix labs-jj-web --profile labs

# Update service to previous version
aws ecs update-service --cluster labs-jj-cluster --service labs-jj-web --task-definition labs-jj-web:[REVISION] --profile labs
```

Or trigger a new GitHub Actions deployment from a known-good commit.

## Maintenance

### Regular Tasks

**Updates to application code:**

- Merge changes to your branch
- GitHub Actions deploys automatically (or manually trigger)

**Sync from upstream (`dimagi/commcare-connect`):**

```bash
# On main branch
git pull origin main
git push fork main

# On labs-main
git checkout labs-main
git merge main  # Merge upstream changes
# Resolve any conflicts in base.txt/production.txt
git push fork labs-main
```

**Add labs-specific dependencies:**

1. Add to `requirements/labs.in`
2. Compile: `pip-compile labs.in -o labs.txt`
3. Commit and push
4. GitHub Actions will rebuild with new dependencies

### Database Backups

RDS automated backups: turned off

````

### Updating Secrets

**To rotate DATABASE_URL password:**

1. Change RDS password in AWS Console
2. Update secret:
   ```bash
   aws secretsmanager update-secret --secret-id labs-jj-database-url --secret-string "postgres://connectadmin:[NEW_PASSWORD]@..." --profile labs
````

3. Restart services (GitHub Actions deploy or `aws ecs update-service --force-new-deployment`)

## Implementation History

### Initial Setup (November 2025)

1. Created VPC, subnets, internet gateway, security group
2. Provisioned RDS PostgreSQL and ElastiCache Redis
3. Created ECR repository
4. Built and pushed initial Docker image
5. Created IAM roles for ECS tasks
6. Created secrets in Secrets Manager
7. Registered ECS task definitions
8. Created ECS cluster and services
9. Ran initial database migrations
10. Verified deployment

### GitHub Actions Integration (November 2025)

1. Created OIDC provider in AWS IAM for GitHub Actions
2. Created `github-actions-labs-deploy` IAM role with trust policy
3. Added permissions for ECR, ECS, EC2, and Secrets Manager
4. Created `.github/workflows/deploy-labs.yml` workflow
5. Configured workflow to deploy from any branch
6. Set `labs-main` as default branch in GitHub (for Actions UI)

### Security Improvements (November 2025)

1. **Removed hardcoded infrastructure IDs**: Workflow now queries AWS dynamically
2. **Moved DATABASE_URL to Secrets Manager**: Password no longer visible in task definitions
3. **Removed verbose logging**: No infrastructure IDs in GitHub Actions logs
4. **Created dedicated `/migrate` script**: Prevents migration tasks from hanging

### Requirements Structure (November 2025)

1. Created `requirements/labs.in` and `labs.txt` for prototype dependencies
2. Keeps `base.txt` and `production.txt` as pure upstream mirrors
3. Added pandas for audit app prototypes
4. Configured Docker to install base + production + labs

## Future Enhancements

**Potential improvements (not prioritized for labs):**

- **Custom domain**: Register domain and configure Route 53
- **SSL/HTTPS**: Add ALB with ACM certificate
- **Auto-scaling**: Configure based on CPU/memory (if traffic increases)
- **Multi-AZ deployment**: For higher availability (doubles cost)
- **CloudFront CDN**: For static assets
- **Monitoring**: CloudWatch dashboards and alarms
- **CI integration**: Auto-deploy on merge to labs-main

**Note**: Labs prioritizes minimal cost and maintenance over these features.

---

## References

- GitHub Actions Workflow: `.github/workflows/deploy-labs.yml`
- Labs Requirements: `requirements/labs.in` and `labs.txt`
- Migration Script: `docker/migrate`
- CloudWatch Logs: `/ecs/labs-jj-web` and `/ecs/labs-jj-worker`

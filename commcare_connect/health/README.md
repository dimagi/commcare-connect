# Health checks

| Path              | Use                                                                         | Checks                                                  | Auth  |
| ----------------- | --------------------------------------------------------------------------- | ------------------------------------------------------- | ----- |
| `/up`             | Container health check (Kamal, see `deploy/config/deploy.yml`), ALB targets | None: Django is serving requests                        | None  |
| `/export/up`      | Uptime monitor for the pool that serves the `/export/` API                  | None: same view as `/up`                                | None  |
| `/status/`        | Status page / uptime monitor                                                | Every database, the cache, Redis (Celery broker)        | Token |
| `/status/celery/` | Status page / uptime monitor                                                | A Celery worker answers a ping and consumes every queue | Token |

`/up` and `/export/up` touch no database or other service, so an outage elsewhere does not get healthy
containers restarted or pulled out of the load balancer. The view opts out of `ATOMIC_REQUESTS`, which would
otherwise open a database connection for every request.

`/export/up` exists only for its URL: the load balancer sends `/export/*` to its own hosts, so a monitor that
polls it through the public hostname learns whether that pool is serving. The load balancer's own target
health checks go straight to each host and should use `/up`.

`/status/` URLs return 200 when every check passes and 500 when any fails, with one line per check. Pick the
format with `?format=json` or `?format=text`, or the `Accept` header. `/status/` leaves out Celery so the web
tier is not reported down because workers are.

When `HEALTH_CHECK_TOKENS` (comma-separated) is set, `/status/` URLs need one of those tokens, either in an
`Authorization: Bearer <token>` header (preferred: it keeps the token out of URLs and access logs) or as
`?token=<token>`. A missing or wrong token gets a 404. `/up` and `/export/up` are never protected. When the
setting is unset, as in local development, `/status/` is open.

None of these paths are traced in Sentry (`commcare_connect/utils/sentry.py`). A failing `/status/` check is
still a 500, so Django logs it and Sentry records an error.

The checks are defined in `checks.py`, built on [django-health-check](https://codingjoe.dev/django-health-check/).

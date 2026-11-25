# syntax=docker/dockerfile:1

# build-python and the runtime stage must use the same base image: the venv links to the interpreter that
# built it, so a different Python patch release at runtime would run against mismatched compiled extensions.
FROM python:3.11-slim-bookworm AS build-python
COPY --from=ghcr.io/astral-sh/uv:0.9.30 /uv /bin/
RUN apt-get update \
  # dependencies for building Python packages; psycopg2 compiles against libpq
  && apt-get install -y --no-install-recommends build-essential libpq-dev
WORKDIR /app
# UV_PYTHON_DOWNLOADS=never: build the venv on the image's Python, never on an interpreter uv downloads.
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
  uv sync --frozen --no-dev --no-install-project --group production

FROM node:26-bookworm AS build-node
WORKDIR /app
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm \
  npm ci
# Tailwind finds the classes it generates by scanning the source tree, so everything under
# commcare_connect/ (templates, Python, JS) is copied, not just the JS entry points.
COPY webpack ./webpack
COPY tailwind ./tailwind
COPY commcare_connect ./commcare_connect
RUN npm run build

FROM python:3.11-slim-bookworm
ENV PYTHONUNBUFFERED=1
ENV DEBUG=0

RUN apt-get update \
  # libpq5 for psycopg2; gettext; pango/harfbuzz for weasyprint; binutils, gdal-bin (which pulls in GEOS
  # and PROJ) for GeoDjango; curl for Kamal's healthcheck; postgresql-client provides psql for `manage.py dbshell`
  && apt-get install -y --no-install-recommends libpq5 gettext curl libpango-1.0-0 libpangoft2-1.0-0 \
    libharfbuzz-subset0 binutils gdal-bin postgresql-client \
  && rm -rf /var/lib/apt/lists/*

RUN addgroup --system django \
    && adduser --system --ingroup django django

ENV DJANGO_SETTINGS_MODULE=config.settings.staging

COPY --from=build-node /app/commcare_connect/static/bundles /app/commcare_connect/static/bundles
COPY --from=build-python /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

WORKDIR /app

COPY --chmod=755 ./docker/* /

# The code and collected static files stay owned by root: the django user only needs to read them, and a
# process that can't write its own code can't be made to rewrite it.
COPY . /app

RUN python /app/manage.py collectstatic --noinput

ARG APP_RELEASE="dev"
ENV APP_RELEASE=${APP_RELEASE}

USER django

EXPOSE 8000

ENTRYPOINT ["/entrypoint"]

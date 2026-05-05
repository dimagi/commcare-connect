FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim AS build-python
RUN apt-get update \
  # dependencies for building Python packages
  && apt-get install -y build-essential libpq-dev
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project --group production

FROM node:18-bullseye AS build-node
#RUN apt-get update && apt-get -y install curl
#RUN curl -sL https://deb.nodesource.com/setup_18.x | bash -
#RUN apt-get install -y nodejs
RUN nodejs -v && npm -v
WORKDIR /app
COPY . /app
RUN npm install
RUN npm run build

FROM python:3.11-slim-bookworm
ENV PYTHONUNBUFFERED=1
ENV DEBUG=0

RUN apt-get update \
  # psycopg2, gettext geodjango etc dependencies
  && apt-get install -y libpq-dev gettext curl libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 binutils libproj-dev gdal-bin \
  # cleaning up unused files
  && apt-get purge -y --auto-remove -o APT::AutoRemove::RecommendsImportant=false \
  && rm -rf /var/lib/apt/lists/*

RUN addgroup --system django \
    && adduser --system --ingroup django django

ENV DJANGO_SETTINGS_MODULE=config.settings.staging

COPY --from=build-node /app/commcare_connect/static/bundles /app/commcare_connect/static/bundles
COPY --from=build-python /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

WORKDIR /app

COPY ./docker/* /
RUN chmod +x /entrypoint /start*
RUN chown django /entrypoint /start*

ARG APP_RELEASE="dev"
ENV APP_RELEASE=${APP_RELEASE}

COPY --chown=django:django . /app

RUN python /app/manage.py collectstatic --noinput
RUN chown django:django -R staticfiles

USER django

EXPOSE 8000

ENTRYPOINT ["/entrypoint"]

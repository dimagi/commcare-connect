FROM python:3.11-slim-bookworm as build-python
RUN apt-get update \
  # dependencies for building Python packages
  && apt-get install -y build-essential libpq-dev
COPY ./requirements /requirements
RUN pip wheel --no-cache-dir --no-deps --wheel-dir /wheels \
    -r /requirements/base.txt \
    -r /requirements/production.txt

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
ENV PYTHONUNBUFFERED 1
ENV DEBUG 0

RUN apt-get update \
  # psycopg2, gettext etc dependencies
  && apt-get install -y libpq-dev gettext curl \
  # cleaning up unused files
  && apt-get purge -y --auto-remove -o APT::AutoRemove::RecommendsImportant=false \
  && rm -rf /var/lib/apt/lists/*

RUN addgroup --system django \
    && adduser --system --ingroup django django

ENV DJANGO_SETTINGS_MODULE=config.settings.labs

COPY --from=build-node /app/commcare_connect/static/bundles /app/commcare_connect/static/bundles
COPY --from=build-python /wheels /wheels
COPY ./requirements /requirements
RUN pip install --no-index --find-links=/wheels \
    -r /requirements/base.txt \
    -r /requirements/production.txt \
    && rm -rf /wheels \
    && rm -rf /root/.cache/pip/*

WORKDIR /app

COPY ./docker/* /
RUN chmod +x /entrypoint /start* /migrate
RUN chown django /entrypoint /start* /migrate

COPY --chown=django:django . /app

RUN python /app/manage.py collectstatic --noinput
RUN chown django:django -R staticfiles

USER django

EXPOSE 8000

ENTRYPOINT ["/entrypoint"]

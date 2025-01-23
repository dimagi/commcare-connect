# CommCare Connect

CommCare Connect

[![Built with Cookiecutter Django](https://img.shields.io/badge/built%20with-Cookiecutter%20Django-ff69b4.svg?logo=cookiecutter)](https://github.com/cookiecutter/cookiecutter-django/)
[![Black code style](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/ambv/black)

## Local setup

This assumes you will use the docker compose file in this repo to run services. If that is not the case you may
need to edit some settings.

    # create and active a python vertual environment using Python 3.11
    $ python3.11 -m venv <virtual env path>

    # install requirements
    $ pip install -r requirements-dev.txt

    # install git hooks
    $ pre-commit install
    $ pre-commit run -a

    # create env file and edit the settings as needed (or export settings directly)
    $ cp .env_template .env

    # start docker services
    $ inv up

    # install JS dependencies
    $ npm ci

    # build JS (optionally watch files for changes and rebuild)
    $ inv build-js [-w]

    # run Django
    $ ./manage.py migrate
    $ ./manage.py runserver

## Basic Commands

Some useful command are available via the `tasks.py` file:

    $ inv -l

### Setting up auth with CommCare HQ

**Expose your local service to the internet**

- Create an account on ngrok and Install ngrok
  - https://dashboard.ngrok.com/get-started/setup/
- Create a custom domain on ngrok using this [link](https://dashboard.ngrok.com/domains/new)
- Run `ngrok http --url=[my-unique-subdomain].ngrok-free.app 8000`
- Update your `.env` file with the host:

      DJANGO_ALLOWED_HOSTS=[my-unique-subdomain].ngrok-free.app

**Create an OAuth2 application on CommCare HQ**

- Navigate to https://staging.commcarehq.org/oauth/applications/
- Create a new application with the following settings:
  - Client Type: Confidential
  - Authorization Grant Type: Authorization Code
  - Redirect URIs: https://[my-unique-subdomain].ngrok-free.app/accounts/commcarehq/login/callback/
  - Pkce required: False
- Copy the Client ID and Client Secret
- Create a new SocialApp locally at http://localhost:8000/admin/socialaccount/socialapp/

**Test the OAuth2 flow**

- Set `COMMCARE_HQ_URL=https://staging.commcarehq.org` in your `.env` file and restart the server.
- Navigate to http://[my-unique-subdomain].ngrok-free.app/accounts/login/
- Click the "Log in with CommCare HQ" button
- You should be redirected to CommCare HQ to log in
- After logging in, you should be redirected back to the app and logged in

### Setting Up Your Users

- To create a **normal user account**, just go to Sign Up and fill out the form. Once you submit it, you'll see a "Verify Your E-mail Address" page. Go to your console to see a simulated email verification message. Copy the link into your browser. Now the user's email should be verified and ready to go.

- To create a **superuser account**, use this command:

      $ python manage.py createsuperuser

- To promote a user to superuser, use this command:

      $ python manage.py promote_user_to_superuser <email>

For convenience, you can keep your normal user logged in on Chrome and your superuser logged in on Firefox (or similar), so that you can see how the site behaves for both kinds of users.

### Test coverage

To run the tests, check your test coverage, and generate an HTML coverage report:

    $ coverage run -m pytest
    $ coverage html
    $ open htmlcov/index.html

#### Running tests with pytest

    $ pytest

### Live reloading and Sass CSS compilation

    $ inv build-js -w

### Celery

This app comes with Celery.

To run a celery worker:

```bash
celery -A config.celery_app worker -l info
```

Please note: For Celery's import magic to work, it is important _where_ the celery commands are run. If you are in the same folder with _manage.py_, you should be right.

To run [periodic tasks](https://docs.celeryq.dev/en/stable/userguide/periodic-tasks.html), you'll need to start the celery beat scheduler service. You can start it as a standalone process:

```bash
celery -A config.celery_app beat
```

or you can embed the beat service inside a worker with the `-B` option (not recommended for production use):

```bash
celery -A config.celery_app worker -B -l info
```

## Deployment

The following details how to deploy this application.

The application is running on AWS. Deploying new version of the app can be done via the "Deploy" workflow
on Github Actions.

Should the deploy fail you can view the logs via the [AWS console][aws_console].

[aws_console]: https://docs.aws.amazon.com/elasticbeanstalk/latest/dg/using-features.logging.html?icmpid=docs_elasticbeanstalk_console

For details on how this actions is configured see:

- https://aws.amazon.com/blogs/security/use-iam-roles-to-connect-github-actions-to-actions-in-aws/
- https://docs.github.com/en/actions/deployment/security-hardening-your-deployments/configuring-openid-connect-in-amazon-web-services

### Deploying to the staging environment

The project has a staging environment at [https://connect-staging.dimagi.com/](https://connect-staging.dimagi.com/),
which is connected to the staging environment of CommCare HQ at
[https://staging.commcarehq.org/](https://staging.commcarehq.org/).

By convention, the `pkv/staging` branch is used for changes that are on the staging environment.
To put your own changes on the staging environment, you can create merge your own branch into
`pkv/staging` and then push it to GitHub.

After that, you can deploy to the staging environment by manually running the `deploy`
[workflow from here](https://github.com/dimagi/commcare-connect/actions/workflows/deploy.yml).

### Custom Bootstrap Compilation

The generated CSS is set up with automatic Bootstrap recompilation with variables of your choice.
Bootstrap v5 is installed using npm and customised by tweaking your variables in `static/sass/custom_bootstrap_vars`.

You can find a list of available variables [in the bootstrap source](https://github.com/twbs/bootstrap/blob/v5.1.3/scss/_variables.scss), or get explanations on them in the [Bootstrap docs](https://getbootstrap.com/docs/5.1/customize/sass/).

Bootstrap's javascript as well as its dependencies are concatenated into a single file: `static/js/vendors.js`.

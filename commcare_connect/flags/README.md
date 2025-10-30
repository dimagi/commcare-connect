# Release Toggles in Commcare Connect

Commcare Connect uses [django waffle](https://waffle.readthedocs.io/en/stable/) to manage feature release toggles.

## Expectations

- Connect exclusively uses switches over other models in waffle, to allow global release of features without any additional targeting.
- Switches should be as short lived as possible, existing through the release, but removed once the feature is out.
- All switches should have a detailed description in the note field of the model, describing the feature they control.

## Configuration Details

- Connect uses the django admin to manage the backend models and enable or disable switches.
- Connect uses the `WAFFLE_CREATE_MISSING_SWITCHES` so that switches are automatically added to the database when they are encountered in the codebase. However, manually adding them prior to deploy is preferred.

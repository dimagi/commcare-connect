# Release Toggles in Commcare Connect

Commcare Connect uses [django waffle](https://waffle.readthedocs.io/en/stable/) to manage feature release toggles.

## Expectations

- Connect uses a mix of both switches and a custom flag model. This allows for global releases, as well as targeted releases to specific users, organizations, opportunities, and/or programs.
- Switches and toggles should be as short lived as possible, existing through the release, but removed once the feature is out.
- All switches and flags should have a detailed description in the note field of the model, describing the feature they control.

## Configuration Details

- Connect uses the django admin to manage the backend models and enable or disable switches and flags.
- Connect uses the `WAFFLE_CREATE_MISSING_SWITCHES` so that switches are automatically added to the database when they are encountered in the codebase (specifically when using `switch_is_active()`). However, manually adding them prior to deploy is preferred.
- Flags will only be auto-added to the database if you use the `flag_is_active` from `commcare_connect.flags.utils` method, but similarly, manually adding them prior to deploy is preferred.

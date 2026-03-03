# Release Toggles in Commcare Connect

Commcare Connect uses [django waffle](https://waffle.readthedocs.io/en/stable/) to manage feature release toggles.

## Expectations

- Connect uses a mix of both switches and a custom flag model. This allows for global releases, as well as targeted releases to specific users, organizations, opportunities, and/or programs.
- Switches and toggles should be as short lived as possible, existing through the release, but removed once the feature is out.
- All switches and flags should have a detailed description in the note field of the model, describing the feature they control.

## Configuration Details

- Connect uses the django admin to manage the backend models and enable or disable switches and flags.
- Connect uses the `WAFFLE_CREATE_MISSING_SWITCHES` so that switches are automatically added to the database when they are encountered in the codebase (specifically when using `switch_is_active()`). However, manually adding them prior to deploy is preferred.
- For flags however, these will not automatically be added to the database (as the standard `flag_is_active()` waffle function will generally not be used). Rather, the custom `is_active_for()` method on the custom `Flag` model will be used, and flags should be created prior to deploy.

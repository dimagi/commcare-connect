# Release Toggles in CommCare Connect

CommCare Connect uses [django waffle](https://waffle.readthedocs.io/en/stable/) to manage feature release.

Waffle provides different tools to control access to a feature on the environment.

For now, features are being released directly to users so new switches & flags should be added only when necessary.

## Usage

Connect uses both [switches](https://waffle.readthedocs.io/en/stable/types/switch.html) and [flags](https://waffle.readthedocs.io/en/stable/types/flag.html).

- While _switches_ are used to turn a feature **on or off for everyone**, _flags_ are used to enable a feature for **specific users, groups, users meeting certain criteria** (such as being authenticated, or superusers).
- This allows for global releases, as well as targeted releases to specific users, organizations, opportunities, and/or programs.

## Expectations

- Switches and flags should be as **short-lived** as possible, existing through the release, but removed once the feature is out.
- All switches and flags should have a **detailed description** in the note field of the model, describing the feature they control.

## Configuration Details

Switches and Flags are actual Django models and not just set in code.

They can be managed by users with required access

- by navigating to "Toggles & Switches" under "Internal Features"
- via Django Admin

### Switches

- switch names are added in the file `switch_names.py`
- use `switch_is_active` to check if switch is enabled
- for new switches, they can be created prior to deploy via Django Admin or added via migration with the release. However, `WAFFLE_CREATE_MISSING_SWITCHES` is set to automatically add new switches to database when they are encountered in the codebase. Description should be added as a followup if created automatically.

### Flags

Connect uses a custom Flag model `commcare_connect.flags.models.Flag` to define its own entities for access

- flags names are added in the file `flag_names.py`
- use `is_active_for()` method on the custom `Flag` model to check access to a feature
- for new flags, they **should** be created on the relevant environment prior to deploy via Django Admin or added via a migration with the release

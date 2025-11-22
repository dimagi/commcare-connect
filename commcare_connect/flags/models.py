from waffle.models import AbstractUserFlag


# This model is not in use but is defined in case we want to use custom user flags in the future.
# See https://waffle.readthedocs.io/en/stable/types/flag.html#custom-flag-models
class Flag(AbstractUserFlag):
    pass

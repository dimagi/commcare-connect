import factory
from waffle.models import Switch

from commcare_connect.flags.models import Flag


class SwitchFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Switch

    name = factory.Faker("bothify", text="SWITCH_????####", letters="ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    active = True


class FlagFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Flag

    name = factory.Faker("bothify", text="flag_????####", letters="abcdefghijklmnopqrstuvwxyz")
    everyone = False

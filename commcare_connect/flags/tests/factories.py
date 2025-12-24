import factory
from waffle.models import Switch


class SwitchFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Switch

    name = factory.Faker("bothify", text="SWITCH_????####", letters="ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    active = True

from django import template

from commcare_connect.utils.local_testing import simulator_enabled

register = template.Library()


@register.simple_tag
def submission_simulator_enabled():
    return simulator_enabled()

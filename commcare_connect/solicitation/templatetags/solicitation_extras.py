from django import template

from commcare_connect.solicitation.permissions import can_manage_solicitations

register = template.Library()

register.filter("can_manage_solicitations", can_manage_solicitations)

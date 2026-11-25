from django import template

from commcare_connect.organization.decorators import is_user_pm_org_admin

register = template.Library()

register.filter("is_pm_org_admin", is_user_pm_org_admin)

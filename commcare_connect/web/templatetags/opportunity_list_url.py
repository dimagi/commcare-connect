from django import template

from commcare_connect.opportunity.utils.opportunity_list import opportunity_list_url

register = template.Library()
register.simple_tag(opportunity_list_url)

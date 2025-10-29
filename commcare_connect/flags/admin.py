from django.contrib import admin
from waffle.admin import FlagAdmin

from .models import Flag

admin.site.register(Flag, FlagAdmin)

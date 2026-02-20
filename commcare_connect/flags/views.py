from django.contrib.auth.decorators import login_required, permission_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from waffle.models import Switch

from commcare_connect.flags.forms import FlagForm
from commcare_connect.flags.models import Flag
from commcare_connect.utils.permission_const import PRODUCT_FEATURES_ACCESS


@login_required
@permission_required(PRODUCT_FEATURES_ACCESS)
def feature_flags(request):
    flags = Flag.objects.prefetch_related("users", "organizations", "programs", "opportunities").order_by("name")
    flag_forms = [(flag, FlagForm(instance=flag)) for flag in flags]
    switches = Switch.objects.order_by("name")

    return render(
        request,
        "flags/feature_flags.html",
        {
            "flag_forms": flag_forms,
            "switches": switches,
        },
    )


@login_required
@permission_required(PRODUCT_FEATURES_ACCESS)
@require_POST
def toggle_switch(request, switch_name):
    switch = get_object_or_404(Switch, name=switch_name)
    switch.active = not switch.active
    switch.save()
    return redirect("flags:feature_flags")


@login_required
@permission_required(PRODUCT_FEATURES_ACCESS)
@require_POST
def update_flag(request, flag_name):
    flag = get_object_or_404(Flag, name=flag_name)
    form = FlagForm(request.POST, instance=flag)
    if form.is_valid():
        form.save()
    return redirect("flags:feature_flags")

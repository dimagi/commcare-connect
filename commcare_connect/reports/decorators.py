from functools import wraps

from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.exceptions import PermissionDenied

from commcare_connect.utils.permission_const import KPI_REPORT_ACCESS


class KPIReportMixin(LoginRequiredMixin, UserPassesTestMixin):
    def test_func(self):
        return self.request.user.has_perm(KPI_REPORT_ACCESS)


def kpi_report_access_required(view_func):
    @login_required
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if request.user.has_perm(KPI_REPORT_ACCESS):
            return view_func(request, *args, **kwargs)
        raise PermissionDenied()

    return _wrapped_view

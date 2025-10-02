from functools import wraps

from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.exceptions import PermissionDenied

KPI_PERMISSION_NAME = "users.kpi_report_access"


class KPIReportMixin(LoginRequiredMixin, UserPassesTestMixin):
    def test_func(self):
        return self.request.user.has_perm(KPI_PERMISSION_NAME)


def kpi_report_access_required(view_func):
    @login_required
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if request.user.has_perm(KPI_PERMISSION_NAME):
            return view_func(request, *args, **kwargs)
        raise PermissionDenied()

    return _wrapped_view

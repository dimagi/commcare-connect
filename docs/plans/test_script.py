from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from django.utils.timezone import now

from commcare_connect.opportunity.models import (
    CompletedWork,
    CompletedWorkStatus,
    UserVisit,
    VisitValidationStatus,
)
from commcare_connect.opportunity.tests.factories import (
    CompletedWorkFactory,
    OpportunityAccessFactory,
    UserVisitFactory,
)

SCRIPT_PATH = Path(__file__).parent / "script.py"
SCRIPT_SRC = SCRIPT_PATH.read_text()


def run_script(xform_ids):
    """Exec docs/plans/script.py with XFORM_IDS substituted."""
    src = SCRIPT_SRC.replace("XFORM_IDS = []", f"XFORM_IDS = {list(xform_ids)!r}", 1)
    exec(compile(src, str(SCRIPT_PATH), "exec"), {"__name__": "_test_script"})


@pytest.fixture
def mock_recompute():
    with patch("commcare_connect.opportunity.tasks.bulk_update_payment_accrued.delay") as m:
        yield m


def _make_visits(num_visits=1, **visit_kwargs):
    access = OpportunityAccessFactory()
    cw = CompletedWorkFactory(opportunity_access=access, status=CompletedWorkStatus.approved)
    visits = UserVisitFactory.create_batch(
        num_visits,
        opportunity=access.opportunity,
        opportunity_access=access,
        user=access.user,
        completed_work=cw,
        status=VisitValidationStatus.approved,
        **visit_kwargs,
    )
    return access, cw, visits


@pytest.mark.django_db
def test_flips_visits_and_completed_work_to_over_limit(mock_recompute):
    access, cw, visits = _make_visits(num_visits=2)

    run_script([v.xform_id for v in visits])

    cw.refresh_from_db()
    assert cw.status == CompletedWorkStatus.over_limit
    for v in visits:
        v.refresh_from_db()
        assert v.status == VisitValidationStatus.over_limit
    mock_recompute.assert_called_once_with(access.opportunity_id, [access.user_id])


@pytest.mark.django_db
def test_status_modified_date_is_stamped(mock_recompute):
    """`.update()` bypasses the model's __setattr__, so the script must pass
    status_modified_date explicitly. Verify it does."""
    access, cw, [visit] = _make_visits(num_visits=1)
    before = now()

    run_script([visit.xform_id])

    visit.refresh_from_db()
    cw.refresh_from_db()
    assert visit.status_modified_date >= before
    assert cw.status_modified_date >= before


@pytest.mark.django_db
def test_empty_xform_ids_is_a_noop(mock_recompute):
    _, _, [visit] = _make_visits(num_visits=1)

    run_script([])

    visit.refresh_from_db()
    assert visit.status == VisitValidationStatus.approved
    mock_recompute.assert_not_called()


@pytest.mark.django_db
def test_unknown_xform_ids_makes_no_changes(mock_recompute):
    _, _, [visit] = _make_visits(num_visits=1)

    run_script(["00000000-0000-0000-0000-000000000000"])

    visit.refresh_from_db()
    assert visit.status == VisitValidationStatus.approved
    mock_recompute.assert_not_called()


@pytest.mark.django_db
def test_visit_without_completed_work_still_flips(mock_recompute):
    """Trial-style visit (completed_work=None): visit flips, no CW touched."""
    access = OpportunityAccessFactory()
    visit = UserVisitFactory(
        opportunity=access.opportunity,
        opportunity_access=access,
        user=access.user,
        completed_work=None,
        status=VisitValidationStatus.trial,
    )

    run_script([visit.xform_id])

    visit.refresh_from_db()
    assert visit.status == VisitValidationStatus.over_limit
    mock_recompute.assert_called_once_with(access.opportunity_id, [access.user_id])


@pytest.mark.django_db
def test_user_dedupe_across_multiple_visits_same_user(mock_recompute):
    """Three visits for one worker should yield a single user_id in the
    recompute call, not three duplicates."""
    access, cw, visits = _make_visits(num_visits=3)

    run_script([v.xform_id for v in visits])

    opp_id, user_ids = mock_recompute.call_args.args
    assert opp_id == access.opportunity_id
    assert user_ids == [access.user_id]


@pytest.mark.django_db
def test_atomicity_rolls_back_visit_flip_when_cw_update_fails(mock_recompute, monkeypatch):
    """If the CW update raises, the visit flip in the same transaction must
    roll back (otherwise we land in the misleading visits=over_limit /
    CW=approved state)."""
    access, cw, [visit] = _make_visits(num_visits=1)

    failing_qs = MagicMock()
    failing_qs.update.side_effect = RuntimeError("boom")
    monkeypatch.setattr(CompletedWork.objects, "filter", lambda **kw: failing_qs)

    with pytest.raises(RuntimeError):
        run_script([visit.xform_id])

    visit.refresh_from_db()
    assert visit.status == VisitValidationStatus.approved
    mock_recompute.assert_not_called()

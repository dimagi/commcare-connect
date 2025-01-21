from commcare_connect.opportunity.models import CompletedWork, CompletedWorkStatus


def validate_saved_fields(completed_work: CompletedWork):
    assert completed_work.saved_completed_count == completed_work.completed_count
    assert completed_work.saved_approved_count == completed_work.approved_count
    if completed_work.status == CompletedWorkStatus.approved:
        assert completed_work.saved_payment_accrued == completed_work.payment_accrued
    else:
        assert completed_work.saved_payment_accrued == 0
    # usd to usd should be the same
    assert completed_work.saved_payment_accrued_usd == completed_work.saved_payment_accrued
    # todo: also validate org payments and currency transfers

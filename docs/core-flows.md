# Core flows

The flows Connect exists to run. A defect in one of these blocks organisations or workers from doing their job, loses submitted work, or records a wrong status, count or amount, however small the change that caused it. Review changes that reach them closely; the `pr-impact` skill (`.claude/skills/pr-impact/`) grades any such change **High** on its core flow dimension.

The code listed is where to start checking whether a change reaches a flow; it is not the whole flow. All paths are under `commcare_connect/`.

**Keep this page current.** When you move, rename or delete code listed here, or add a flow, update this page in the same PR.

## Field work: claim → learn → deliver

| Flow                                                                                        | Start from                                                                                                                                                                                                       |
| ------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Claim an opportunity**: worker claims, claim limits allocated                             | `ClaimOpportunityView` in `opportunity/api/views/mobile.py`; `OpportunityClaim`, `OpportunityClaimLimit` in `opportunity/models/access.py`                                                                       |
| **Form ingest**: HQ forwards a form, Connect records learn progress, assessments and visits | `process_xform`, `process_learn_form`, `process_deliver_form`, `process_deliver_unit` in `form_receiver/processor.py`; `form_receiver/README.md`                                                                 |
| **Visit status at ingest**: duplicate/GPS/limit flags, over-limit checks                    | `process_deliver_unit`, `check_visit_over_limit`, `clean_form_submission` in `form_receiver/processor.py`; `UserVisit` in `opportunity/models/visits.py`; `opportunity/models/verification.py`; `utils/flags.py` |
| **Visit review**: approving and rejecting visits, bulk status import                        | `bulk_update_visit_status` in `opportunity/visit_import.py`; `bulk_update_visit_status_task` in `opportunity/tasks.py`                                                                                           |

## Money: accrual → invoice → payment

See `opportunity/PAYMENTS.md` for the rules these flows follow.

| Flow                                                   | Start from                                                                                                                                                 |
| ------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Payment accrual**: completed work counted and priced | `CompletedWork` in `opportunity/models/completed_work.py`; `update_status` in `opportunity/utils/completed_work.py`; `opportunity/models/payment_units.py` |
| **Invoicing**: billing approved units into invoices    | `opportunity/models/invoices.py`; `opportunity/utils/invoice.py`, `invoice_line_items.py`; `opportunity/views/invoices.py`                                 |
| **Recording payments**: payments to workers and orgs   | `Payment` in `opportunity/models/invoices.py`; `invoice_pay`, `invoice_update_status` in `opportunity/views/invoices.py`                                   |

## Identity and access

| Flow                                                                                                                                                                                                         | Start from                                                                                                                     |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------ |
| **Worker identity**: ConnectID users linked to HQ users                                                                                                                                                      | `ConnectIDUserLink` in `users/models.py`; `connect_id_client/main.py`; `get_user` in `form_receiver/processor.py`              |
| **Org access**: who can see and change an org's opportunities. A defect that locks people out of their work is a core-flow risk; one that grants access to the wrong people is graded under Security instead | `organization/decorators.py` (`opp_*_access_required`, `user_is_org_admin`); `OrganizationMiddleware` in `users/middleware.py` |

## Adding a flow

Add a flow here when a defect in it would stop organisations or workers doing their job, or would make Connect record wrong money or work. Give it the code to start from.

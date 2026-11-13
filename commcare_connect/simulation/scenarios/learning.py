"""Workers at every learn stage: invited only, part way through, finished but not claimed, failed the assessment,
and claimed."""

from commcare_connect.simulation.steps.setup import LEARN_PASSING_SCORE

from . import _common

TITLE = "Learning"
ORG_NAMES = _common.org_names(TITLE)

FAILING_SCORE = LEARN_PASSING_SCORE - 30


def build(sim, *, small):
    program, nm_org = _common.program(sim, TITLE)
    opportunity = _common.opportunity(sim, program, nm_org, f"{TITLE} Opportunity")
    per_stage = 1 if small else 3

    _common.trained_workers(sim, opportunity, per_stage)
    _common.trained_workers(sim, opportunity, per_stage, claim=False)
    with sim.at(_common.days_ago(_common.INVITE_DAYS_AGO)):
        failed = sim.invite(opportunity, per_stage)
    with sim.at(_common.days_ago(_common.LEARN_DAYS_AGO)):
        sim.learn(opportunity, failed, score=FAILING_SCORE)
    # Invited last week; some have opened the learn app since.
    with sim.at(_common.days_ago(7)):
        started = sim.invite(opportunity, per_stage)
        sim.invite(opportunity, per_stage)
    with sim.at(_common.days_ago(2)):
        sim.learn(opportunity, started, complete=0.4)
    _common.link_page(sim, opportunity, "learning", "opportunity:worker_learn")

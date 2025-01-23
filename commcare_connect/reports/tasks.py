from config import celery_app

from .views import get_quarters_since_start, get_table_data_for_quarter


@celery_app.task()
def prime_report_cache():
    quarters = get_quarters_since_start()
    for q in quarters:
        get_table_data_for_quarter(q, "", False)
        get_table_data_for_quarter(q, "", True)

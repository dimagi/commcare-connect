#!/usr/bin/env python
"""
Audit Integration Test Script

This script validates the complete audit creation workflow end-to-end using REAL data.
It simulates the full UI workflow: search → select → preview → create

Workflow Steps:
    1. Search for opportunities (by keyword)
    2. Select opportunities (by strategy: top_by_visits, first, random)
    3. Preview audit (generates counts and samples if configured)
    4. Create audit sessions (uses cached sample from preview)
    5. Verify results

Requirements:
    - SUPERSET_* environment variables configured
    - COMMCARE_USERNAME and COMMCARE_API_KEY for image downloads
    - Django settings configured

Usage:
    python commcare_connect/audit/run_audit_integration.py [config_key]

    Available configs:
        baseline_100pct  - Search "readers", select top 2, no sampling (100%)
        sampling_50pct   - Search "readers", select top 2, 50% sampling
        sampling_25pct   - Search "readers", select top 2, 25% sampling
        random_flws_50   - Search "readers", top 2, 2 random FLWs, last 50 per FLW
        random_flws_all  - Search "readers", top 2, 5 random FLWs, ALL visits per FLW
"""

import os
import sys
from dataclasses import dataclass, field

# Add project root to Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

# Configure Django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")

import django  # noqa: E402

django.setup()

from django.contrib.auth import get_user_model  # noqa: E402

from commcare_connect.audit.management.extractors.connect_api_facade import ConnectAPIFacade  # noqa: E402
from commcare_connect.audit.models import Audit  # noqa: E402
from commcare_connect.audit.services.audit_creator import create_audit_sessions, preview_audit_sessions  # noqa: E402
from commcare_connect.audit.services.database_manager import reset_audit_database  # noqa: E402
from commcare_connect.opportunity.models import Opportunity, UserVisit  # noqa: E402

User = get_user_model()


# ============================================================================
# CONFIGURATION
# ============================================================================


@dataclass
class AuditRunConfig:
    """
    Configuration for an audit integration test run.

    This configuration defines all parameters needed to run a complete
    audit creation workflow from search to completion.
    """

    # Display
    name: str  # Human-readable name for this configuration

    # --- Search & Selection ---
    # These parameters control how opportunities are discovered and selected
    search_query: str  # Keyword to search for opportunities (e.g., "readers", "nutrition")
    select_strategy: str  # How to select from results: 'top_by_visits', 'first', 'random'
    select_count: int = 1  # Number of opportunities to select from search results

    # --- Audit Scope ---
    # These parameters define what visits to include in the audit
    audit_type: str = "last_n_per_flw"  # 'date_range', 'last_n_per_flw', 'last_n_across_opp'
    granularity: str = "per_flw"  # 'combined', 'per_opp', 'per_flw'
    count_per_flw: int = None  # Number of visits per FLW (for last_n_per_flw type)
    count_across_opp: int = None  # Total visits across opportunity (for last_n_across_opp type)
    start_date: str = None  # Start date for date_range type (YYYY-MM-DD)
    end_date: str = None  # End date for date_range type (YYYY-MM-DD)

    # --- Sampling ---
    # Control what percentage of matching visits to include
    sample_percentage: int = 100  # Percentage to sample (1-100, default 100 = no sampling)

    # --- FLW Selection ---
    # Control which FLWs to include in the audit
    random_flw_count: int = None  # If set, randomly select N FLWs instead of all FLWs

    # --- Runtime Options ---
    auditor_username: str = "integration_test"  # Username of person running the test
    clear_db_before: bool = True  # Whether to clear database before running

    # --- Internal State ---
    # This field is populated dynamically during execution
    opportunity_ids: list[int] = field(default_factory=list)  # Populated from search results


# Predefined test configurations
RUN_CONFIGS = {
    "baseline_100pct": AuditRunConfig(
        name="Baseline Run - 100% (No Sampling)",
        search_query="readers",
        select_strategy="top_by_visits",
        select_count=2,
        count_per_flw=5,
        sample_percentage=100,
    ),
    "sampling_50pct": AuditRunConfig(
        name="Sampling Run - 50%",
        search_query="readers",
        select_strategy="top_by_visits",
        select_count=2,
        count_per_flw=5,
        sample_percentage=50,
    ),
    "sampling_25pct": AuditRunConfig(
        name="Sampling Run - 25%",
        search_query="readers",
        select_strategy="top_by_visits",
        select_count=2,
        count_per_flw=5,
        sample_percentage=25,
    ),
    "test_across_all": AuditRunConfig(
        name="Test Last N Across All - 1000 total",
        search_query="chc",
        select_strategy="top_by_visits",
        select_count=3,
        audit_type="last_n_across_all",
        granularity="combined",
        count_across_opp=1000,
        sample_percentage=100,
    ),
    "test_sampling_3pct": AuditRunConfig(
        name="Test Sampling - CHC Top 3, 10k visits, 3% sampling",
        search_query="chc",
        select_strategy="top_by_visits",
        select_count=3,
        audit_type="last_n_across_all",
        granularity="combined",
        count_across_opp=10000,
        sample_percentage=3,
    ),
    "troubleshoot_last2": AuditRunConfig(
        name="Troubleshoot - Readers Top 1, Last 2 per FLW",
        search_query="readers",
        select_strategy="top_by_visits",
        select_count=1,
        audit_type="last_n_per_flw",
        granularity="per_flw",
        count_per_flw=2,
        sample_percentage=100,
    ),
    "fastest": AuditRunConfig(
        name="Fastest - 1 Opp, 1 FLW, Last 5 Visits",
        search_query="readers",
        select_strategy="top_by_visits",
        select_count=1,
        audit_type="last_n_per_flw",
        granularity="per_flw",
        count_per_flw=5,
        sample_percentage=100,
        random_flw_count=1,  # Only 1 random FLW for speed
    ),
    "random_flws_50": AuditRunConfig(
        name="Random FLWs - Readers Top 2, 2 Random FLWs, Last 50 per FLW",
        search_query="readers",
        select_strategy="top_by_visits",
        select_count=2,
        audit_type="last_n_per_flw",
        granularity="per_flw",
        count_per_flw=50,
        sample_percentage=100,
        random_flw_count=2,  # Randomly select 2 FLWs
    ),
    "random_flws_all": AuditRunConfig(
        name="Random FLWs - Readers Top 2, 5 Random FLWs, ALL Visits per FLW",
        search_query="readers",
        select_strategy="top_by_visits",
        select_count=2,
        audit_type="last_n_per_flw",
        granularity="per_flw",
        count_per_flw=99999,  # Large number to get all visits
        sample_percentage=100,
        random_flw_count=5,  # Randomly select 5 FLWs
    ),
}


# ============================================================================
# WORKFLOW STEPS
# ============================================================================


def clear_database():
    """Clear all audit-related data from the database."""
    print("\n" + "=" * 80)
    print("CLEARING DATABASE")
    print("=" * 80)

    # Count before deletion
    counts_before = {
        "opportunities": Opportunity.objects.count(),
        "visits": UserVisit.objects.count(),
        "audits": Audit.objects.count(),
        "users_with_visits": len(list(UserVisit.objects.values_list("user_id", flat=True).distinct())),
    }

    print("\nBefore deletion:")
    for key, count in counts_before.items():
        print(f"  {key}: {count}")

    # Use the same reset function that the UI uses
    deleted = reset_audit_database()

    print("\nDeleted:")
    for key, count in deleted.items():
        print(f"  {key}: {count}")

    # Verify database is empty
    counts_after = {
        "opportunities": Opportunity.objects.count(),
        "visits": UserVisit.objects.count(),
        "audits": Audit.objects.count(),
    }

    print("\nAfter deletion:")
    for key, count in counts_after.items():
        print(f"  {key}: {count}")

    all_zero = all(count == 0 for count in counts_after.values())
    if all_zero:
        print("\n[OK] Database is empty and ready")
    else:
        print("\n[WARNING] Database is not completely empty!")

    return all_zero


def search_opportunities(facade: ConnectAPIFacade, search_query: str) -> list:
    """
    Search for opportunities using a keyword.

    Args:
        facade: Authenticated API facade
        search_query: Keyword to search for

    Returns:
        List of matching Opportunity objects
    """
    print(f"\n> Searching for opportunities matching '{search_query}'...")
    opportunities = facade.search_opportunities(search_query, limit=50)

    if not opportunities:
        print("  [ERROR] No opportunities found")
        return []

    print(f"  [OK] Found {len(opportunities)} opportunities")
    return opportunities


def select_opportunities(opportunities: list, strategy: str, count: int) -> list[int]:
    """
    Select opportunities from search results using specified strategy.

    Args:
        opportunities: List of Opportunity objects from search
        strategy: Selection strategy ('top_by_visits', 'first', 'random')
        count: Number of opportunities to select

    Returns:
        List of selected opportunity IDs
    """
    print(f"\n> Selecting {count} opportunity(ies) using '{strategy}' strategy...")

    if strategy == "top_by_visits":
        selected = sorted(opportunities, key=lambda o: o.visit_count, reverse=True)[:count]
    elif strategy == "first":
        selected = opportunities[:count]
    elif strategy == "random":
        import random

        selected = random.sample(opportunities, min(count, len(opportunities)))
    else:
        print(f"  [ERROR] Unknown selection strategy: {strategy}")
        return []

    print(f"  [OK] Selected {len(selected)} opportunity(ies):")
    for opp in selected:
        print(f"    - {opp.name} (ID: {opp.id}, Visits: {opp.visit_count})")

    return [opp.id for opp in selected]


def build_criteria(config: AuditRunConfig) -> dict:
    """
    Build criteria dictionary for preview and creation from config.

    Args:
        config: Audit run configuration

    Returns:
        Criteria dictionary for audit services
    """
    criteria = {
        "type": config.audit_type,
        "granularity": config.granularity,
        "samplePercentage": config.sample_percentage,
    }

    if config.audit_type == "last_n_per_flw":
        criteria["countPerFlw"] = config.count_per_flw
    elif config.audit_type == "last_n_per_opp":
        criteria["countPerOpp"] = config.count_per_opp
    elif config.audit_type == "last_n_across_all":
        criteria["countAcrossAll"] = config.count_across_opp
    elif config.audit_type == "date_range":
        criteria["startDate"] = config.start_date
        criteria["endDate"] = config.end_date

    return criteria


def preview_audit(facade: ConnectAPIFacade, config: AuditRunConfig):
    """
    Generate preview of what will be created.

    Args:
        facade: Authenticated API facade
        config: Audit run configuration with opportunity_ids populated

    Returns:
        Preview result object (contains cache key if sampling)
    """
    print("\n> Generating preview...")

    criteria = build_criteria(config)
    result = preview_audit_sessions(facade=facade, opportunity_ids=config.opportunity_ids, criteria=criteria)

    if not result.success:
        print(f"  [ERROR] {result.error}")
        return None

    # Display preview results
    for preview in result.preview_data:
        print(f"\n  Opportunity: {preview['opportunity_name']} (ID: {preview['opportunity_id']})")
        print(f"    - FLWs: {preview['total_flws']}")
        if preview.get("total_visits_before_sampling"):
            print(f"    - Visits (before sampling): {preview['total_visits_before_sampling']}")
            print(f"    - Visits (after {preview['sample_percentage']}% sampling): {preview['total_visits']}")
        else:
            print(f"    - Visits: {preview['total_visits']}")
        print(f"    - Avg visits/FLW: {preview['avg_visits_per_flw']}")
        print(f"    - Sessions to create: {preview['sessions_to_create']}")

    print("\n  [OK] Preview generated successfully")
    return result


def create_audit(facade: ConnectAPIFacade, config: AuditRunConfig, preview_result):
    """
    Create audits using cached sample from preview.

    Args:
        facade: Authenticated API facade
        config: Audit run configuration
        preview_result: Result from preview step (contains template)

    Returns:
        Creation result object
    """
    print("\n> Creating audits...")

    criteria = build_criteria(config)

    # If sampling was used, add the cache key from preview
    if preview_result and preview_result.preview_data:
        for preview in preview_result.preview_data:
            if preview.get("sample_cache_key"):
                criteria["sampleCacheKey"] = preview["sample_cache_key"]
                print("  Using cached sample from preview")
                break

    # Get the template from the preview result if available
    template = preview_result.template if preview_result else None

    # Handle random FLW selection if configured
    selected_flw_user_ids = None
    if config.random_flw_count and config.granularity == "per_flw":
        print(f"\n  Selecting {config.random_flw_count} random FLWs...")
        import random

        # Get all available FLWs
        all_flws = facade.get_unique_flws_across_opportunities(config.opportunity_ids)
        print(f"  Found {len(all_flws)} total FLWs")

        if len(all_flws) <= config.random_flw_count:
            print(f"  [WARNING] Only {len(all_flws)} FLWs available, using all")
            selected_flw_user_ids = [flw["user_id"] for flw in all_flws]
        else:
            # Randomly select N FLWs
            selected_flws = random.sample(all_flws, config.random_flw_count)
            selected_flw_user_ids = [flw["user_id"] for flw in selected_flws]
            print("  Selected FLWs:")
            for flw in selected_flws:
                print(f"    - {flw['username']} (ID: {flw['user_id']})")

    result = create_audit_sessions(
        facade=facade,
        opportunity_ids=config.opportunity_ids,
        criteria=criteria,
        auditor_username=config.auditor_username,
        selected_flw_user_ids=selected_flw_user_ids,
        audit_definition=template,
    )

    if not result.success:
        print(f"  [ERROR] {result.error}")
        return None

    print(f"\n  [OK] Created {result.audits_created} audit(s)")

    # Display statistics
    stats = result.stats
    print("\n  Statistics:")
    print(f"    - Opportunities created: {stats.get('opportunities_created', 0)}")
    print(f"    - Users created: {stats.get('users_created', 0)}")
    print(f"    - Visits loaded: {stats.get('visits_created', 0)}")
    print(f"    - Attachments downloaded: {stats.get('attachments_downloaded', 0)}")

    return result


def verify_audit(result) -> bool:
    """
    Verify that audits were created correctly.

    Args:
        result: Creation result object

    Returns:
        True if all audits are valid, False otherwise
    """
    print("\n> Verifying audits...")

    if not result or not result.audits:
        print("  [ERROR] No audits to verify")
        return False

    audits = result.audits
    all_valid = True

    for audit in audits:
        audit.refresh_from_db()

        # Check status
        if audit.status != Audit.Status.IN_PROGRESS:
            print(f"  [ERROR] Audit {audit.id}: Wrong status ({audit.status})")
            all_valid = False
            continue

        # Check visits
        visit_count = audit.visits.count()
        if visit_count == 0:
            print(f"  [ERROR] Audit {audit.id}: No visits found")
            all_valid = False
        else:
            print(f"  [OK] Audit {audit.id} ({audit.flw_username}): {visit_count} visits")

    if all_valid:
        print("\n  [OK] All audits verified successfully")
    else:
        print("\n  [ERROR] Some audits have issues")

    return all_valid


# ============================================================================
# MAIN WORKFLOW
# ============================================================================


def run_audit_integration(config: AuditRunConfig) -> bool:
    """
    Execute complete audit integration workflow.

    This is the main entry point that orchestrates all workflow steps:
    1. Clear database (optional)
    2. Search for opportunities
    3. Select opportunities from results
    4. Preview audit
    5. Create audit
    6. Verify results

    Args:
        config: Audit run configuration

    Returns:
        True if workflow completed successfully, False otherwise
    """
    print("\n" + "=" * 80)
    print(f"AUDIT INTEGRATION: {config.name}")
    print("=" * 80)
    print("\nConfiguration:")
    print(f"  Search: '{config.search_query}' -> {config.select_count} ({config.select_strategy})")
    print(f"  Audit: {config.audit_type} / {config.granularity}")
    if config.count_per_flw:
        print(f"  Visits: {config.count_per_flw} per FLW")
    print(f"  Sampling: {config.sample_percentage}%")

    # Initialize facade
    facade = ConnectAPIFacade()
    if not facade.authenticate():
        print("\n[ERROR] Failed to authenticate with data source")
        print("Check SUPERSET_* environment variables")
        return False

    try:
        # Step 0: Clear database (optional)
        if config.clear_db_before:
            if not clear_database():
                return False

        # Step 1: Search
        print("\n" + "=" * 80)
        print("STEP 1: SEARCH FOR OPPORTUNITIES")
        print("=" * 80)
        opportunities = search_opportunities(facade, config.search_query)
        if not opportunities:
            return False

        # Step 2: Select
        print("\n" + "=" * 80)
        print("STEP 2: SELECT OPPORTUNITIES")
        print("=" * 80)
        config.opportunity_ids = select_opportunities(opportunities, config.select_strategy, config.select_count)
        if not config.opportunity_ids:
            return False

        # Step 3: Preview
        print("\n" + "=" * 80)
        print("STEP 3: PREVIEW AUDIT")
        print("=" * 80)
        preview_result = preview_audit(facade, config)
        if not preview_result:
            return False

        # Step 4: Create
        print("\n" + "=" * 80)
        print("STEP 4: CREATE AUDIT")
        print("=" * 80)
        create_result = create_audit(facade, config, preview_result)
        if not create_result:
            return False

        # Step 5: Verify
        print("\n" + "=" * 80)
        print("STEP 5: VERIFY RESULTS")
        print("=" * 80)
        success = verify_audit(create_result)

        # Final status
        print("\n" + "=" * 80)
        if success:
            print(f"[SUCCESS] {config.name} completed successfully")
        else:
            print(f"[ERROR] {config.name} completed with errors")
        print("=" * 80)

        return success

    finally:
        facade.close()


def main():
    """Parse arguments and run the configured test."""
    config_key = sys.argv[1] if len(sys.argv) > 1 else "fastest"

    if config_key not in RUN_CONFIGS:
        print(f"[ERROR] Unknown config: {config_key}")
        print("\nUsage: python run_audit_integration.py [config_key]")
        print("\nAvailable configurations:")
        for key, cfg in RUN_CONFIGS.items():
            print(f"  {key}: {cfg.name}")
            print(f"    Search: '{cfg.search_query}' → top {cfg.select_count} ({cfg.select_strategy})")
            print(f"    Sampling: {cfg.sample_percentage}%")
        return 1

    config = RUN_CONFIGS[config_key]
    success = run_audit_integration(config)
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())

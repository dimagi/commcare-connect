"""
Audit Creation Service

This service orchestrates the complete audit creation workflow:
1. Creating opportunities
2. Loading users
3. Loading visits based on criteria
4. Creating audit sessions (with support for different granularities)
5. Downloading attachments

Updated to use AuditDefinition as a first-class object.
"""

from typing import Any

from commcare_connect.audit.management.extractors.connect_api_facade import ConnectAPIFacade
from commcare_connect.audit.models import Audit, AuditTemplate
from commcare_connect.audit.services.data_loader import AuditDataLoader
from commcare_connect.audit.services.progress_tracker import ProgressTracker


class AuditCreationResult:
    """Result of audit creation operation"""

    def __init__(
        self,
        success: bool,
        audits: list[Audit] = None,
        stats: dict = None,
        error: str = None,
        template: AuditTemplate = None,
    ):
        self.success = success
        self.audits = audits or []
        self.stats = stats or {}
        self.error = error
        self.template = template

    @property
    def audits_created(self) -> int:
        return len(self.audits)

    @property
    def first_audit(self) -> Audit | None:
        return self.audits[0] if self.audits else None

    # Backward compatibility properties
    @property
    def sessions(self):
        return self.audits

    @property
    def sessions_created(self):
        return self.audits_created

    @property
    def first_session(self):
        return self.first_audit

    @property
    def audit_definition(self):
        return self.template


class AuditPreviewResult:
    """Result of audit preview operation"""

    def __init__(
        self,
        success: bool,
        template: AuditTemplate = None,
        error: str = None,
    ):
        self.success = success
        self.template = template
        self.error = error

    # Backward compatibility properties
    @property
    def audit_definition(self):
        return self.template

    @property
    def preview_data(self) -> list[dict]:
        return self.template.preview_data if self.template else []

    @property
    def sampled_visit_ids_cache_key(self) -> str | None:
        # sample_cache_key was removed from the model
        return None


def create_audit_sessions(
    facade: ConnectAPIFacade,
    opportunity_ids: list[int],
    criteria: dict[str, Any],
    auditor_username: str,
    limit_flws: int | None = None,
    progress_tracker: ProgressTracker | None = None,
    audit_definition: AuditTemplate | None = None,
    selected_flw_user_ids: list[str] | None = None,
    audit_title: str = "",
    audit_tag: str = "",
    user=None,
) -> AuditCreationResult:
    """
    Create audit sessions based on provided criteria.

    Args:
        facade: Authenticated ConnectAPIFacade instance
        opportunity_ids: List of opportunity IDs to audit
        criteria: Dictionary containing audit criteria:
            - type: 'date_range', 'last_n_per_flw', or 'last_n_across_opp'
            - granularity: 'combined', 'per_opp', or 'per_flw'
            - startDate/endDate: For date_range type
            - countPerFlw: For last_n_per_flw type
            - countAcrossOpp: For last_n_across_opp type
        auditor_username: Username of person creating the audit
        limit_flws: Optional limit on number of FLWs to process (for testing)
        progress_tracker: Optional progress tracker
        audit_definition: Optional AuditTemplate to link audits to
        user: User object for the auditor (if None, will look up from auditor_username)

    Returns:
        AuditCreationResult with audits, stats, and success status
    """
    try:
        # Get auditor User object
        if not user:
            from django.contrib.auth import get_user_model

            User = get_user_model()
            try:
                user = User.objects.get(username=auditor_username)
            except User.DoesNotExist:
                # If user doesn't exist, create a placeholder or use admin
                user = User.objects.filter(is_superuser=True).first()
                if not user:
                    return AuditCreationResult(success=False, error=f"User '{auditor_username}' not found")
        # Extract audit parameters
        audit_type = criteria.get("type")
        granularity = criteria.get("granularity", "combined")
        start_date = criteria.get("startDate") if audit_type == "date_range" else None
        end_date = criteria.get("endDate") if audit_type == "date_range" else None
        count_per_flw = criteria.get("countPerFlw") if audit_type == "last_n_per_flw" else None
        count_per_opp = criteria.get("countPerOpp") if audit_type == "last_n_per_opp" else None
        count_across_all = criteria.get("countAcrossAll") if audit_type == "last_n_across_all" else None

        # Get sampled visit IDs from template (sample_cache_key field was removed)
        sampled_visit_ids = None
        if audit_definition and audit_definition.sampled_visit_ids:
            sampled_visit_ids = audit_definition.sampled_visit_ids
            print(f"[INFO] Using pre-sampled visit IDs from template ({len(sampled_visit_ids)} visits)")

        # Initialize data loader
        loader = AuditDataLoader(facade=facade, dry_run=False)

        # Step 1: Create minimal opportunities for FK integrity
        if progress_tracker:
            progress_tracker.update(0, 100, "Loading opportunities...", "loading", step_name="opportunities")
        print("  Creating opportunities...")
        opportunities = loader.create_minimal_opportunities(opportunity_ids)
        if progress_tracker:
            progress_tracker.complete_step("opportunities", "Opportunities loaded")

        # Build opportunity name for display
        def get_opportunity_name(opp_ids: list[int]) -> str:
            """Get a display name for one or more opportunities"""
            opp_list = [opportunities.get(opp_id) for opp_id in opp_ids if opportunities.get(opp_id)]
            if not opp_list:
                return f"{len(opp_ids)} opportunities"
            elif len(opp_list) == 1:
                return opp_list[0].name
            else:
                # For multiple, show first 2 and count if more
                names = [opp.name for opp in opp_list[:2]]
                if len(opp_list) > 2:
                    return f"{', '.join(names)} (+{len(opp_list) - 2} more)"
                return ", ".join(names)

        # Step 2: Load users
        if progress_tracker:
            progress_tracker.update(0, 100, "Loading users...", "loading", step_name="users")
        print("  Loading users from Superset...")
        users = loader.load_users(opportunity_ids)
        print(f"  Loaded {len(users)} users")
        if progress_tracker:
            progress_tracker.complete_step("users", f"Loaded {len(users)} users")

        # Step 3: Load visits and create audits based on granularity
        if progress_tracker:
            progress_tracker.update(0, 100, "Loading visits and creating audits...", "processing", step_name="audits")
        created_audits = []
        all_visits = []  # Track all visits for image downloading

        if granularity == "combined":
            # One audit for all opportunities combined
            if progress_tracker:
                progress_tracker.update(0, 100, "Loading visits...", "processing", step_name="sessions")

            # Load visits - either from cache (sampled) or fresh query
            if sampled_visit_ids:
                visits = loader.load_visits_by_ids(sampled_visit_ids)
            else:
                visits = loader.load_visits(
                    opportunity_ids=opportunity_ids,
                    audit_type=audit_type,
                    start_date=start_date,
                    end_date=end_date,
                    count=count_per_flw or count_per_opp or count_across_all,
                )

            # Filter visits by selected FLWs if specified
            if selected_flw_user_ids:
                visits = [v for v in visits if v.user_id in selected_flw_user_ids]
                print(f"  Filtered to {len(visits)} visits from {len(selected_flw_user_ids)} selected FLWs")

            all_visits.extend(visits)

            if progress_tracker:
                progress_tracker.update(50, 100, "Creating audit session...", "processing", step_name="sessions")

            # Calculate actual date range from loaded visits
            session_start = start_date
            session_end = end_date
            if visits and (start_date is None or end_date is None):
                visit_dates = [
                    v.visit_date.date() if hasattr(v.visit_date, "date") else v.visit_date
                    for v in visits
                    if v.visit_date
                ]
                if visit_dates:
                    session_start = min(visit_dates)
                    session_end = max(visit_dates)

            session = loader.create_audit_session(
                auditor_username=auditor_username,
                opportunity_ids=opportunity_ids,
                granularity="combined",
                audit_type=audit_type,
                start_date=session_start,
                end_date=session_end,
                count=count_per_flw or count_per_opp or count_across_all,
                opportunity_name=get_opportunity_name(opportunity_ids),
                audit_definition=audit_definition,
                audit_title=audit_title,
                audit_tag=audit_tag,
                user=user,
            )
            # Explicitly assign the loaded visits to this session
            session.visits.set(visits)
            created_audits.append(session)

            if progress_tracker:
                progress_tracker.update(100, 100, "Session created", "processing", step_name="sessions")

        elif granularity == "per_opp":
            # One audit per opportunity
            # Special case: last_n_across_all should load ALL visits once, then split by opportunity
            if audit_type == "last_n_across_all" and not sampled_visit_ids:
                # Load all visits once with global limit
                if progress_tracker:
                    progress_tracker.update(
                        0, 100, "Loading visits across all opportunities...", "processing", step_name="sessions"
                    )

                all_loaded_visits = loader.load_visits(
                    opportunity_ids=opportunity_ids,
                    audit_type=audit_type,
                    start_date=start_date,
                    end_date=end_date,
                    count=count_across_all,
                )
                all_visits.extend(all_loaded_visits)

                # Group visits by opportunity for per_opp sessions
                from collections import defaultdict

                visits_by_opp = defaultdict(list)
                for visit in all_loaded_visits:
                    visits_by_opp[visit.opportunity_id].append(visit)

                # Create one session per opportunity with its visits
                for idx, opp_id in enumerate(opportunity_ids, 1):
                    if progress_tracker:
                        progress_tracker.update(
                            idx,
                            len(opportunity_ids),
                            f"Creating audit for opportunity {idx}/{len(opportunity_ids)}",
                            "processing",
                            step_name="sessions",
                        )

                    visits = visits_by_opp.get(opp_id, [])
                    if not visits:
                        print(f"[WARNING] No visits found for opportunity {opp_id} in last_n_across_all query")
                        continue

                    # Filter visits by selected FLWs if specified
                    if selected_flw_user_ids:
                        visits = [v for v in visits if v.user_id in selected_flw_user_ids]
                        print(f"  Filtered opportunity {opp_id} to {len(visits)} visits from selected FLWs")

                    # Calculate actual date range from loaded visits
                    session_start = start_date
                    session_end = end_date
                    if visits and (start_date is None or end_date is None):
                        visit_dates = [
                            v.visit_date.date() if hasattr(v.visit_date, "date") else v.visit_date
                            for v in visits
                            if v.visit_date
                        ]
                        if visit_dates:
                            session_start = min(visit_dates)
                            session_end = max(visit_dates)

                    session = loader.create_audit_session(
                        auditor_username=auditor_username,
                        opportunity_ids=[opp_id],
                        granularity="per_opp",
                        audit_type=audit_type,
                        start_date=session_start,
                        end_date=session_end,
                        count=count_across_all,
                        opportunity_name=get_opportunity_name([opp_id]),
                        audit_definition=audit_definition,
                        audit_title=audit_title,
                        audit_tag=audit_tag,
                        user=user,
                    )
                    session.visits.set(visits)
                    created_audits.append(session)
            else:
                # Normal per_opp flow (for other audit types or when using cached sample)
                for idx, opp_id in enumerate(opportunity_ids, 1):
                    if progress_tracker:
                        progress_tracker.update(
                            idx,
                            len(opportunity_ids),
                            f"Creating audit for opportunity {idx}/{len(opportunity_ids)}",
                            "processing",
                            step_name="sessions",
                        )

                    # Load visits - either from cache (sampled) or fresh query
                    if sampled_visit_ids:
                        # Filter sampled IDs for this opportunity
                        # We need to check which IDs belong to this opportunity
                        visits = loader.load_visits_by_ids(sampled_visit_ids)
                        visits = [v for v in visits if v.opportunity_id == opp_id]
                    else:
                        visits = loader.load_visits(
                            opportunity_ids=[opp_id],
                            audit_type=audit_type,
                            start_date=start_date,
                            end_date=end_date,
                            count=count_per_flw or count_per_opp or count_across_all,
                        )

                    # Filter visits by selected FLWs if specified
                    if selected_flw_user_ids:
                        visits = [v for v in visits if v.user_id in selected_flw_user_ids]
                        print(f"  Filtered opportunity {opp_id} to {len(visits)} visits from selected FLWs")

                    all_visits.extend(visits)

                # Calculate actual date range from loaded visits
                session_start = start_date
                session_end = end_date
                if visits and (start_date is None or end_date is None):
                    visit_dates = [
                        v.visit_date.date() if hasattr(v.visit_date, "date") else v.visit_date
                        for v in visits
                        if v.visit_date
                    ]
                    if visit_dates:
                        session_start = min(visit_dates)
                        session_end = max(visit_dates)

                session = loader.create_audit_session(
                    auditor_username=auditor_username,
                    opportunity_ids=[opp_id],
                    granularity="per_opp",
                    audit_type=audit_type,
                    start_date=session_start,
                    end_date=session_end,
                    count=count_per_flw or count_per_opp or count_across_all,
                    opportunity_name=get_opportunity_name([opp_id]),
                    audit_definition=audit_definition,
                    audit_title=audit_title,
                    audit_tag=audit_tag,
                    user=user,
                )
                # Explicitly assign the loaded visits to this session
                session.visits.set(visits)
                created_audits.append(session)

        elif granularity == "per_flw":
            # One audit per FLW across all opportunities
            print("  Fetching FLW list from Superset...")
            flws = facade.get_unique_flws_across_opportunities(opportunity_ids)
            print(f"  Found {len(flws)} FLWs")

            # Filter by selected FLW user IDs if provided
            if selected_flw_user_ids:
                flws = [flw for flw in flws if flw.get("user_id") in selected_flw_user_ids]
                print(f"  Filtered to {len(flws)} selected FLWs")

            # Apply limit if specified (useful for integration testing)
            if limit_flws and len(flws) > limit_flws:
                flws = flws[:limit_flws]

            print(f"  Creating audit sessions for {len(flws)} FLWs...")
            for idx, flw in enumerate(flws, 1):
                # Check for cancellation
                if progress_tracker and progress_tracker.is_cancelled():
                    print("  Audit creation cancelled by user")
                    return AuditCreationResult(success=False, error="Cancelled by user")

                user_id = flw["user_id"]

                if idx % 10 == 1 or idx == len(flws):
                    print(f"    Processing FLW {idx}/{len(flws)} ({flw.get('username', 'unknown')})")

                # Update progress for per-FLW mode
                if progress_tracker and len(flws) > 1:
                    progress_tracker.update(
                        idx,
                        len(flws),
                        f"Creating audit for FLW {idx}/{len(flws)}: {flw.get('username', 'unknown')}",
                        "processing",
                        step_name="sessions",
                    )

                # Load visits - either from cache (sampled) or fresh query
                if sampled_visit_ids:
                    # Filter sampled IDs for this FLW
                    visits = loader.load_visits_by_ids(sampled_visit_ids)
                    visits = [v for v in visits if v.user_id == user_id]
                else:
                    visits = loader.load_visits(
                        opportunity_ids=opportunity_ids,
                        audit_type=audit_type,
                        start_date=start_date,
                        end_date=end_date,
                        count=count_per_flw or count_per_opp or count_across_all,
                        user_id=user_id,
                    )
                all_visits.extend(visits)

                # Determine which opportunities this FLW actually has visits in
                flw_opportunity_ids = list({v.opportunity_id for v in visits if v.opportunity_id})

                # Skip if no visits found for this FLW
                if not visits or not flw_opportunity_ids:
                    continue

                # Calculate actual date range from loaded visits (important for last_n type audits)
                session_start = start_date
                session_end = end_date
                if visits and (start_date is None or end_date is None):
                    visit_dates = [
                        v.visit_date.date() if hasattr(v.visit_date, "date") else v.visit_date
                        for v in visits
                        if v.visit_date
                    ]
                    if visit_dates:
                        session_start = min(visit_dates)
                        session_end = max(visit_dates)

                session = loader.create_audit_session(
                    auditor_username=auditor_username,
                    opportunity_ids=flw_opportunity_ids,  # Use only the opportunities this FLW has visits in
                    granularity="per_flw",
                    audit_type=audit_type,
                    start_date=session_start,
                    end_date=session_end,
                    count=count_per_flw or count_per_opp or count_across_all,
                    flw_username=flw.get("username"),
                    opportunity_name=get_opportunity_name(
                        flw_opportunity_ids
                    ),  # Generate name from FLW's actual opportunities
                    audit_definition=audit_definition,
                    audit_title=audit_title,
                    audit_tag=audit_tag,
                    user=user,
                )
                # Explicitly assign the loaded visits to this session
                session.visits.set(visits)
                created_audits.append(session)

        # Mark sessions step as complete
        if progress_tracker:
            progress_tracker.complete_step("sessions", f"Created {len(created_audits)} session(s)")

        # Step 4: Download attachments synchronously for all loaded visits
        # Remove duplicates (visits may be loaded multiple times in per_flw mode)
        unique_visits = list({v.id: v for v in all_visits}.values())

        print(f"[INFO] Total visits loaded: {len(all_visits)}, unique visits: {len(unique_visits)}")
        if sampled_visit_ids:
            print(f"[INFO] Using pre-sampled visits (expected: {len(sampled_visit_ids)} visits)")

        # Check for cancellation before downloading
        if progress_tracker and progress_tracker.is_cancelled():
            print("  Audit creation cancelled by user before downloading attachments")
            return AuditCreationResult(success=False, error="Cancelled by user")

        # Download with progress tracking (the download_attachments method will handle progress updates)
        if progress_tracker:
            progress_tracker.update(
                0, len(unique_visits) or 1, "Starting attachment download...", "downloading", step_name="attachments"
            )
        loader.download_attachments(unique_visits, progress_tracker=progress_tracker)

        # Mark attachments step as complete
        if progress_tracker:
            progress_tracker.complete_step("attachments", "All attachments downloaded")

        # Step 5: Generate assessments for all sessions (after attachments are downloaded)
        print("[INFO] Generating assessments for all audit sessions...")
        from commcare_connect.audit.services.assessment_generator import generate_assessments_for_session

        total_assessments = 0
        for session in created_audits:
            assessment_stats = generate_assessments_for_session(session)
            total_assessments += assessment_stats["assessments_created"]
            if progress_tracker:
                progress_tracker.log(
                    f"Session {session.id}: Generated {assessment_stats['assessments_created']} assessments "
                    f"for {assessment_stats['images_processed']} images"
                )

        print(f"[OK] Generated {total_assessments} assessments across {len(created_audits)} session(s)")

        # Complete progress tracking
        if progress_tracker:
            progress_tracker.update(100, 100, "Audit creation completed!", "complete")

        # Get statistics
        stats = loader.get_stats()

        return AuditCreationResult(success=True, audits=created_audits, stats=stats, template=audit_definition)

    except Exception as e:
        return AuditCreationResult(success=False, error=str(e))


def preview_audit_sessions(
    facade: ConnectAPIFacade,
    opportunity_ids: list[int],
    criteria: dict[str, Any],
    progress_tracker: ProgressTracker | None = None,
    user=None,
) -> AuditPreviewResult:
    """
    Preview what audits would be created based on criteria.

    This creates an AuditTemplate object containing all audit parameters,
    preview statistics, and sampled visit IDs (if sampling is enabled).

    Args:
        facade: Authenticated ConnectAPIFacade instance
        opportunity_ids: List of opportunity IDs to audit
        criteria: Dictionary containing audit criteria
            - type: Audit type (date_range, last_n_per_flw, etc.)
            - granularity: combined, per_opp, or per_flw
            - samplePercentage: Optional percentage to sample (1-100, default 100)
        progress_tracker: Optional progress tracker for reporting progress
        user: User creating the audit template

    Returns:
        AuditPreviewResult containing the created AuditTemplate
    """
    try:
        print(f"[PREVIEW] Raw criteria received: {criteria}")

        if progress_tracker:
            progress_tracker.update_step("loading_ids", 0, "in_progress", "Loading visit IDs...")
        # Extract audit parameters (same as create_audit_sessions)
        audit_type = criteria.get("type")
        granularity = criteria.get("granularity", "combined")
        start_date = criteria.get("startDate") if audit_type == "date_range" else None
        end_date = criteria.get("endDate") if audit_type == "date_range" else None
        count_per_flw = criteria.get("countPerFlw") if audit_type == "last_n_per_flw" else None
        count_per_opp = criteria.get("countPerOpp") if audit_type == "last_n_per_opp" else None
        count_across_all = criteria.get("countAcrossAll") if audit_type == "last_n_across_all" else None
        sample_percentage = int(criteria.get("samplePercentage", 100))

        print("[PREVIEW] Parsed parameters:")
        print(f"  - audit_type: {audit_type}")
        print(f"  - granularity: {granularity}")
        print(f"  - count_across_all: {count_across_all} (type: {type(count_across_all)})")
        print(f"  - sample_percentage: {sample_percentage}")

        # Check if we need to sample (reduce the visit set)
        needs_sampling = sample_percentage < 100

        # ALWAYS load visit IDs for export/import consistency across systems
        # Even at 100%, we need to know exactly which visits we're auditing
        sample_cache_key = None
        sampled_visit_ids = None
        sampled_visit_counts_by_opp = {}  # Track actual sampled counts per opportunity

        print(f"[INFO] Loading visit IDs (sample: {sample_percentage}%)...")
        if progress_tracker:
            progress_tracker.update_step(
                "loading_ids", 10, "in_progress", f"Loading visit IDs ({sample_percentage}% sampling)..."
            )

        # Load lightweight visit IDs (fast, minimal memory)
        # Track which opportunity each visit belongs to for accurate preview counts
        visit_id_to_opp = {}  # Map visit_id -> opportunity_id
        all_visit_ids = []

        if granularity == "per_flw":
            # For per_flw, we need to load IDs per FLW
            flws = facade.get_unique_flws_across_opportunities(opportunity_ids)
            for flw in flws:
                user_id = flw["user_id"]
                visit_ids = facade.get_user_visit_ids_for_audit(
                    opportunity_ids=opportunity_ids,
                    audit_type=audit_type,
                    start_date=start_date,
                    end_date=end_date,
                    count=count_per_flw or count_per_opp or count_across_all,
                    user_id=user_id,
                )
                all_visit_ids.extend(visit_ids)
        else:
            # For combined/per_opp, load visit IDs
            if audit_type == "last_n_across_all":
                # For last_n_across_all, query ALL opportunities at once so LIMIT applies globally
                # Returns list of tuples (visit_id, opportunity_id)
                result = facade.get_user_visit_ids_for_audit(
                    opportunity_ids=opportunity_ids,  # All opportunities at once
                    audit_type=audit_type,
                    start_date=start_date,
                    end_date=end_date,
                    count=count_across_all,
                )
                # Unpack tuples: result is [(visit_id, opp_id), ...]
                for visit_id, opp_id in result:
                    visit_id_to_opp[visit_id] = opp_id
                    all_visit_ids.append(visit_id)
            else:
                # For other types (date_range, last_n_per_opp), query per opportunity
                for opp_id in opportunity_ids:
                    visit_ids = facade.get_user_visit_ids_for_audit(
                        opportunity_ids=[opp_id],
                        audit_type=audit_type,
                        start_date=start_date,
                        end_date=end_date,
                        count=count_per_flw or count_per_opp or count_across_all,
                    )
                    # Track which opportunity these visits belong to
                    for vid in visit_ids:
                        visit_id_to_opp[vid] = opp_id
                    all_visit_ids.extend(visit_ids)

        # Remove duplicates (visits may appear in multiple opportunity queries)
        all_visit_ids = list(set(all_visit_ids))
        print(f"[INFO] Loaded {len(all_visit_ids)} visit IDs")

        if progress_tracker:
            progress_tracker.update_step("loading_ids", 100, "complete", f"Loaded {len(all_visit_ids)} visit IDs")
            progress_tracker.update_step("sampling", 0, "in_progress", f"Sampling {sample_percentage}% of visits...")

        # Sample the IDs (or use all if 100%)
        import random
        import uuid

        if needs_sampling:
            sample_size = int(len(all_visit_ids) * sample_percentage / 100)
            sampled_visit_ids = (
                random.sample(all_visit_ids, sample_size) if sample_size < len(all_visit_ids) else all_visit_ids
            )
            print(f"[INFO] Sampled {len(sampled_visit_ids)} visit IDs ({sample_percentage}%)")
        else:
            # No sampling needed, but capture all visit IDs for export/import
            sampled_visit_ids = all_visit_ids
            print(f"[INFO] Using all {len(sampled_visit_ids)} visit IDs (no sampling)")

        if progress_tracker:
            progress_tracker.update_step("sampling", 100, "complete", f"Selected {len(sampled_visit_ids)} visits")

        # Count actual visits per opportunity
        for vid in sampled_visit_ids:
            opp_id = visit_id_to_opp.get(vid)
            if opp_id:
                sampled_visit_counts_by_opp[opp_id] = sampled_visit_counts_by_opp.get(opp_id, 0) + 1

        # Cache the visit IDs with 1 hour expiry
        from django.core.cache import cache

        sample_cache_key = f"audit_sample_{uuid.uuid4()}"
        cache.set(sample_cache_key, sampled_visit_ids, timeout=3600)
        print(f"[INFO] Cached visit IDs with key: {sample_cache_key}")

        preview_data = []

        if progress_tracker:
            progress_tracker.update_step("calculating", 0, "in_progress", "Calculating preview...")

        # Special handling for last_n_across_all: show combined preview only
        if audit_type == "last_n_across_all" and granularity == "combined":
            # For last_n_across_all with combined granularity, don't show per-opportunity breakdown
            # Show a single combined preview
            # We always have sampled_visit_ids now (even at 100%)
            visits_to_count = sampled_visit_ids
            total_visits_combined = len(sampled_visit_ids)
            print(f"[PREVIEW] Using {total_visits_combined} selected visits for preview")

            # Calculate unique FLWs from the selected/sampled visits
            print(f"[PREVIEW] Counting unique FLWs for {len(visits_to_count)} visits...")
            unique_flws = 0
            if visits_to_count:
                try:
                    # Query Superset for unique user_ids in these visits
                    # Limit to first 5000 for SQL performance (should be representative)
                    sample_size = min(len(visits_to_count), 5000)
                    visit_ids_str = ",".join(str(vid) for vid in visits_to_count[:sample_size])
                    flw_query = f"""
                    SELECT COUNT(DISTINCT user_id) as flw_count
                    FROM opportunity_uservisit
                    WHERE id IN ({visit_ids_str})
                    """
                    flw_df = facade.superset_extractor.execute_query(flw_query)
                    if flw_df is not None and not flw_df.empty:
                        unique_flws = int(flw_df.iloc[0]["flw_count"])
                    if sample_size < len(visits_to_count):
                        print(
                            f"[PREVIEW] Found {unique_flws} unique FLWs "
                            f"(sampled from {sample_size} of {len(visits_to_count)} visits)"
                        )
                    else:
                        print(f"[PREVIEW] Found {unique_flws} unique FLWs")
                except Exception as e:
                    print(f"[WARNING] Could not count FLWs: {e}")
                    unique_flws = 0

            # Calculate average visits per FLW
            avg_visits_per_flw = round(total_visits_combined / max(unique_flws, 1), 1) if unique_flws > 0 else 0

            # Get combined name
            if len(opportunity_ids) == 1:
                opportunities = facade.search_opportunities(str(opportunity_ids[0]), 1)
                opp_name = opportunities[0].name if opportunities else f"Opportunity {opportunity_ids[0]}"
            else:
                opp_name = f"{len(opportunity_ids)} opportunities"

            preview_item = {
                "opportunity_id": 0,  # Dummy ID for combined view
                "opportunity_name": opp_name,
                "total_flws": unique_flws,
                "total_visits": total_visits_combined,
                "total_visits_before_sampling": count_across_all if needs_sampling else None,
                "sample_percentage": sample_percentage if needs_sampling else None,
                "avg_visits_per_flw": avg_visits_per_flw,
                "date_range": f"Last {count_across_all} visits across all opportunities",
                "sessions_to_create": 1,
                "sample_cache_key": sample_cache_key if needs_sampling else None,
                "flws": [],  # Don't show individual FLW breakdown for combined view
            }
            preview_data.append(preview_item)

        else:
            # Normal flow: show per-opportunity breakdown
            for opp_id in opportunity_ids:
                opportunities = facade.search_opportunities(str(opp_id), 1)
                if not opportunities:
                    continue

                opportunity = opportunities[0]

                # Get FLW and visit counts based on criteria
                # These are the same methods that will be called during actual creation
                if audit_type == "date_range":
                    counts = facade.get_flw_visit_counts_by_date_range(opp_id, start_date, end_date)
                elif audit_type == "last_n_per_flw":
                    counts = facade.get_flw_visit_counts_last_n_per_flw(opp_id, count_per_flw)
                elif audit_type == "last_n_per_opp":
                    counts = facade.get_flw_visit_counts_last_n_across_opp(opp_id, count_per_opp)
                else:
                    continue

                # Calculate how many sessions would be created based on granularity
                sessions_to_create = 1  # Default for combined/per_opp
                if granularity == "per_flw":
                    sessions_to_create = counts["total_flws"]

                # Get actual sampled visit count for this opportunity
                total_visits_before_sampling = counts["total_visits"]
                if needs_sampling and opp_id in sampled_visit_counts_by_opp:
                    # Use ACTUAL sampled count (exact)
                    total_visits_after_sampling = sampled_visit_counts_by_opp[opp_id]
                elif needs_sampling:
                    # Fallback to approximation if tracking failed (e.g., per_flw granularity)
                    total_visits_after_sampling = int(total_visits_before_sampling * sample_percentage / 100)
                else:
                    # No sampling
                    total_visits_after_sampling = total_visits_before_sampling

                # Augment FLW data with prior audit tags
                flws_with_tags = counts.get("flws", [])
                if flws_with_tags:
                    # Get all FLW connect_ids/usernames
                    flw_connect_ids = [flw.get("connect_id") for flw in flws_with_tags if flw.get("connect_id")]

                    # Query for prior completed audits for these FLWs in these opportunities
                    # NOTE: This query will need to be refactored post-migration since flw_username
                    # is now a computed property. For now, query via visits relationship.
                    if flw_connect_ids:
                        from collections import defaultdict

                        prior_tags_by_flw = defaultdict(list)

                        # Query audits via visits to find FLW-specific audits
                        from django.contrib.auth import get_user_model

                        User = get_user_model()
                        flw_users = User.objects.filter(username__in=flw_connect_ids)

                        prior_audits = (
                            Audit.objects.filter(
                                visits__user__in=flw_users,
                                status=Audit.Status.COMPLETED,
                                tag__isnull=False,
                            )
                            .exclude(tag="")
                            .distinct()
                        )

                        for audit in prior_audits:
                            flw_username = audit.flw_username  # Uses computed property
                            if audit.tag and flw_username in flw_connect_ids:
                                prior_tags_by_flw[flw_username].append(audit.tag)

                        # Add prior tags to each FLW in the preview
                        for flw in flws_with_tags:
                            connect_id = flw.get("connect_id")
                            flw["prior_audit_tags"] = list(set(prior_tags_by_flw.get(connect_id, [])))

                preview_item = {
                    "opportunity_id": opp_id,
                    "opportunity_name": opportunity.name,
                    "total_flws": counts["total_flws"],
                    "total_visits": total_visits_after_sampling,  # Show actual sampled count
                    "total_visits_before_sampling": total_visits_before_sampling if needs_sampling else None,
                    "sample_percentage": sample_percentage if needs_sampling else None,
                    "avg_visits_per_flw": round(total_visits_after_sampling / max(counts["total_flws"], 1), 1),
                    "date_range": counts.get("date_range"),
                    "flws": flws_with_tags,
                    "sessions_to_create": sessions_to_create,
                    "granularity": granularity,
                    "audit_type": audit_type,
                }

                # Add cache key to first preview item (it's shared across all opportunities)
                if needs_sampling and sample_cache_key and len(preview_data) == 0:
                    preview_item["sample_cache_key"] = sample_cache_key

                preview_data.append(preview_item)

        if progress_tracker:
            progress_tracker.update_step(
                "calculating", 100, "complete", f"Preview calculated ({len(preview_data)} items)"
            )

        # Clean up all audit templates from this user's session
        # This prevents pollution when user tries different preview configurations
        if user:
            templates_to_delete = AuditTemplate.objects.filter(created_by=user)
            deleted_count = templates_to_delete.count()
            if deleted_count > 0:
                templates_to_delete.delete()
                print(f"[PREVIEW] Cleaned up {deleted_count} audit template(s) from previous previews")

        # Create AuditTemplate to store the preview
        from datetime import date

        template = AuditTemplate(
            created_by=user,
            opportunity_ids=opportunity_ids,
            audit_type=audit_type,
            granularity=granularity,
            start_date=date.fromisoformat(start_date) if start_date else None,
            end_date=date.fromisoformat(end_date) if end_date else None,
            count_per_flw=count_per_flw,
            count_per_opp=count_per_opp,
            count_across_all=count_across_all,
            sample_percentage=sample_percentage,
            sampled_visit_ids=sampled_visit_ids,  # Always store visit IDs for export/import
            preview_data=preview_data,
        )
        template.save()

        print(f"[PREVIEW] Created AuditTemplate {template.id}")

        return AuditPreviewResult(success=True, template=template)

    except Exception as e:
        import traceback

        return AuditPreviewResult(success=False, error=f"{str(e)}\n{traceback.format_exc()}")

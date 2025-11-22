"""
AuditDataLoader Service

This service loads audit data from Superset into Django models.
It handles the complete data pipeline: opportunities, programs, users, visits, and attachments.
"""

from datetime import date, datetime

from django.contrib.auth import get_user_model
from django.utils import timezone

from commcare_connect.audit.management.extractors.connect_api_facade import ConnectAPIFacade
from commcare_connect.audit.models import Audit
from commcare_connect.commcarehq.models import HQServer
from commcare_connect.opportunity.models import CommCareApp, DeliverUnit, Opportunity, UserVisit
from commcare_connect.organization.models import Organization

User = get_user_model()


class AuditDataLoader:
    """
    Service to load audit data from Superset into Django models.

    Handles three granularity levels:
    - combined: One audit for all selected opportunities
    - per_opp: One audit per opportunity
    - per_flw: One audit per field worker
    """

    def __init__(self, facade: ConnectAPIFacade, dry_run: bool = False, limit_visits: int = None):
        """
        Initialize the data loader.

        Args:
            facade: ConnectAPIFacade instance for data extraction
            dry_run: If True, don't actually save to database
            limit_visits: Optional limit on number of visits to load (for testing)
        """
        self.facade = facade
        self.dry_run = dry_run
        self.limit_visits = limit_visits
        self.stats = {
            "opportunities_created": 0,
            "opportunities_updated": 0,
            "programs_created": 0,
            "programs_updated": 0,
            "users_created": 0,
            "users_updated": 0,
            "visits_created": 0,
            "visits_updated": 0,
        }

    def create_minimal_opportunities(self, opportunity_ids: list[int]) -> dict[int, Opportunity]:
        """
        Create opportunity records with deliver_app from Superset.
        We need deliver_app for domain information to download images.

        Args:
            opportunity_ids: List of opportunity IDs

        Returns:
            Dictionary mapping opportunity ID to Opportunity instance
        """
        opportunities = {}

        # Fetch opportunity details from Superset
        opp_data_list = self.facade.get_opportunity_details(opportunity_ids)
        opp_data_dict = {opp["id"]: opp for opp in opp_data_list}

        for opp_id in opportunity_ids:
            if self.dry_run:
                print(f"[DRY RUN] Would create opportunity: {opp_id}")
                opportunities[opp_id] = None
                continue

            # Check if already exists
            existing = Opportunity.objects.filter(id=opp_id).first()
            if existing:
                opportunities[opp_id] = existing
                continue

            # Get opportunity data from Superset
            opp_data = opp_data_dict.get(opp_id)
            if not opp_data:
                error_msg = (
                    f"ERROR: No data found for opportunity {opp_id} in Superset. "
                    "Cannot create audit without opportunity metadata."
                )
                raise ValueError(error_msg)

            # Get or create organization (use slug as lookup since it's unique)
            org, _ = Organization.objects.get_or_create(
                slug="audit-org", defaults={"name": opp_data.get("organization_name", "Audit Organization")}
            )

            # Get or create deliver app (for domain information)
            deliver_app = None
            if opp_data.get("deliver_app_id"):
                from commcare_connect.opportunity.models import CommCareApp, HQServer

                # Get CommCareApp from the deliver units we loaded
                deliver_app = CommCareApp.objects.filter(id=opp_data["deliver_app_id"]).first()

                if not deliver_app:
                    # Need to create it - use existing HQServer
                    hq_server = HQServer.objects.first()
                    if hq_server:
                        # Fetch app details from Superset
                        app_sql = f"""
                        SELECT cc_app_id, name, cc_domain, hq_server_id
                        FROM opportunity_commcareapp
                        WHERE id = {opp_data['deliver_app_id']}
                        """
                        app_df = self.facade.superset_extractor.execute_query(app_sql)
                        if app_df is not None and not app_df.empty:
                            app_row = app_df.iloc[0]
                            deliver_app, _ = CommCareApp.objects.get_or_create(
                                id=opp_data["deliver_app_id"],
                                defaults={
                                    "cc_app_id": app_row.get("cc_app_id", "unknown"),
                                    "name": app_row.get("name", "Unknown App"),
                                    "cc_domain": app_row.get("cc_domain", ""),
                                    "hq_server": hq_server,
                                    "organization": org,
                                    "description": "",
                                },
                            )

            # Create opportunity with deliver_app
            opp = Opportunity.objects.create(
                id=opp_id,
                name=opp_data.get("name", f"Opportunity {opp_id}"),
                description=opp_data.get("description", "Loaded from Superset for audit"),
                organization=org,
                active=opp_data.get("active", True),
                deliver_app=deliver_app,
            )
            opportunities[opp_id] = opp
            self.stats["opportunities_created"] += 1

        return opportunities

    def load_deliver_units(self, opportunity_ids: list[int]) -> dict[int, DeliverUnit]:
        """
        Load DeliverUnit records from Superset (with their CommCareApp and HQServer dependencies).

        Args:
            opportunity_ids: List of opportunity IDs

        Returns:
            Dictionary mapping deliver_unit_id to DeliverUnit instance
        """
        deliver_unit_data = self.facade.get_deliver_units_for_visits(opportunity_ids)

        deliver_units = {}

        for du_data in deliver_unit_data:
            if self.dry_run:
                print(f"[DRY RUN] Would create deliver unit: {du_data.get('name')} (ID: {du_data.get('id')})")
                continue

            # Get or create HQServer (use existing if URL matches, reusing OAuth)
            hq_server = None
            if du_data.get("hq_server_url"):
                hq_server = HQServer.objects.filter(url=du_data["hq_server_url"]).first()
                if not hq_server:
                    # Use first existing HQServer to avoid OAuth issues
                    hq_server = HQServer.objects.first()
                    if not hq_server:
                        print(f"WARNING: No HQServer found - skipping deliver unit {du_data.get('id')}")
                        continue

            # Get or create CommCareApp
            app = None
            if du_data.get("cc_app_id") and hq_server:
                # Get or create organization for audit data
                org, _ = Organization.objects.get_or_create(slug="audit-org", defaults={"name": "Audit Organization"})

                app, _ = CommCareApp.objects.get_or_create(
                    cc_app_id=du_data["cc_app_id"],
                    defaults={
                        "name": du_data.get("app_name") or f"App {du_data['cc_app_id']}",
                        "cc_domain": du_data.get("cc_domain", ""),
                        "hq_server": hq_server,
                        "organization": org,
                        "description": "",
                    },
                )

            # Get or create DeliverUnit
            deliver_unit, created = DeliverUnit.objects.get_or_create(
                id=du_data["id"],
                defaults={
                    "name": du_data.get("name", f"Deliver Unit {du_data['id']}"),
                    "slug": du_data.get("slug", f"du-{du_data['id']}"),
                    "description": du_data.get("description", ""),
                    "optional": du_data.get("optional", True),
                    "app": app,
                },
            )

            deliver_units[du_data["id"]] = deliver_unit

            if created:
                self.stats["deliver_units_created"] = self.stats.get("deliver_units_created", 0) + 1

        return deliver_units

    def load_users(self, opportunity_ids: list[int]) -> dict[int, User]:
        """
        Load users from Superset into Django models.

        Args:
            opportunity_ids: List of opportunity IDs to get users for

        Returns:
            Dictionary mapping user ID to User instance
        """
        user_data_list = self.facade.get_users_for_opportunities(opportunity_ids)
        users = {}

        for user_data in user_data_list:
            if self.dry_run:
                print(f"[DRY RUN] Would create/update user: {user_data.get('username')}")
                continue

            # Get or create user
            user, created = User.objects.update_or_create(
                id=user_data.get("id"),
                defaults={
                    "username": user_data.get("username"),
                    "name": user_data.get("name", ""),
                    "email": user_data.get("email", ""),
                    "phone_number": user_data.get("phone_number", ""),
                    "is_active": user_data.get("is_active", True),
                },
            )

            if created:
                self.stats["users_created"] += 1
            else:
                self.stats["users_updated"] += 1

            users[user.id] = user

        return users

    def load_visits(
        self,
        opportunity_ids: list[int],
        audit_type: str,
        start_date: date = None,
        end_date: date = None,
        count: int = None,
        user_id: int = None,
    ) -> list[UserVisit]:
        """
        Load user visits from Superset into Django models.

        Args:
            opportunity_ids: List of opportunity IDs
            audit_type: 'date_range', 'last_n_per_flw', 'last_n_per_opp', or 'last_n_across_all'
            start_date: Start date for date_range type
            end_date: End date for date_range type
            count: Number of visits for last_n types
            user_id: Optional filter for specific user

        Returns:
            List of created/updated UserVisit instances
        """
        visit_data_list = self.facade.get_user_visits_for_audit(
            opportunity_ids=opportunity_ids,
            audit_type=audit_type,
            start_date=start_date,
            end_date=end_date,
            count=count,
            user_id=user_id,
        )

        # Apply limit if specified
        if self.limit_visits and len(visit_data_list) > self.limit_visits:
            print(f"[WARNING]  Limiting visits from {len(visit_data_list)} to {self.limit_visits} for testing")
            visit_data_list = visit_data_list[: self.limit_visits]

        visits = []

        for visit_data in visit_data_list:
            if self.dry_run:
                print(f"[DRY RUN] Would create/update visit: {visit_data.get('xform_id')}")
                continue

            # Parse visit_date
            visit_date = visit_data.get("visit_date")
            if isinstance(visit_date, str):
                visit_date = datetime.fromisoformat(visit_date.replace("Z", "+00:00"))
            if not timezone.is_aware(visit_date):
                visit_date = timezone.make_aware(visit_date)

            # TODO: Skip form_json for now - it's an expensive download
            # We can add a separate method later if needed:
            # def load_form_json_for_visits(self, visit_ids: list[int]):
            #     """Load full form_json from Superset for specific visits."""
            #     pass

            # Get or create visit
            visit, created = UserVisit.objects.update_or_create(
                xform_id=visit_data.get("xform_id"),
                defaults={
                    "user_id": visit_data.get("user_id"),
                    "opportunity_id": visit_data.get("opportunity_id"),
                    "visit_date": visit_date,
                    "entity_id": visit_data.get("entity_id"),
                    "entity_name": visit_data.get("entity_name"),
                    "location": visit_data.get("location"),
                    "status": visit_data.get("status", "approved"),
                    "reason": visit_data.get("reason"),
                    "flag_reason": visit_data.get("flag_reason"),
                    "flagged": visit_data.get("flagged", False),
                    "form_json": {},  # Skip for now - expensive download
                    "deliver_unit": None,  # Not needed for audit workflow
                },
            )

            if created:
                self.stats["visits_created"] += 1
            else:
                self.stats["visits_updated"] += 1

            # Store domain info as temporary attribute for attachment downloads
            # (not persisted to DB, just used during this session)
            visit._temp_cc_domain = visit_data.get("cc_domain")
            visit._temp_cc_app_id = visit_data.get("cc_app_id")

            visits.append(visit)

        return visits

    def load_visits_by_ids(self, visit_ids: list[int]) -> list[UserVisit]:
        """
        Load specific user visits by their IDs.

        This is used when loading pre-sampled visits during audit creation.

        Args:
            visit_ids: List of visit IDs to load

        Returns:
            List of created/updated UserVisit instances
        """
        visit_data_list = self.facade.get_user_visits_by_ids(visit_ids)

        visits = []

        for visit_data in visit_data_list:
            if self.dry_run:
                print(f"[DRY RUN] Would create/update visit: {visit_data.get('xform_id')}")
                continue

            # Parse visit_date
            visit_date = visit_data.get("visit_date")
            if isinstance(visit_date, str):
                visit_date = datetime.fromisoformat(visit_date.replace("Z", "+00:00"))
            if not timezone.is_aware(visit_date):
                visit_date = timezone.make_aware(visit_date)

            # Get or create visit
            visit, created = UserVisit.objects.update_or_create(
                xform_id=visit_data.get("xform_id"),
                defaults={
                    "user_id": visit_data.get("user_id"),
                    "opportunity_id": visit_data.get("opportunity_id"),
                    "visit_date": visit_date,
                    "entity_id": visit_data.get("entity_id"),
                    "entity_name": visit_data.get("entity_name"),
                    "location": visit_data.get("location"),
                    "status": visit_data.get("status", "approved"),
                    "reason": visit_data.get("reason"),
                    "flag_reason": visit_data.get("flag_reason"),
                    "flagged": visit_data.get("flagged", False),
                    "form_json": {},  # Skip for now - expensive download
                    "deliver_unit": None,  # Not needed for audit workflow
                },
            )

            if created:
                self.stats["visits_created"] += 1
            else:
                self.stats["visits_updated"] += 1

            # Store domain info as temporary attribute for attachment downloads
            visit._temp_cc_domain = visit_data.get("cc_domain")
            visit._temp_cc_app_id = visit_data.get("cc_app_id")

            visits.append(visit)

        return visits

    def create_audit_session(
        self,
        auditor_username: str,
        opportunity_ids: list[int],
        granularity: str,
        audit_type: str,
        start_date: date = None,
        end_date: date = None,
        count: int = None,
        flw_username: str = None,
        opportunity_name: str = None,
        audit_definition=None,
        audit_title: str = "",
        audit_tag: str = "",
        user=None,
    ) -> Audit:
        """
        Create an audit.

        Args:
            auditor_username: Username of person conducting audit (for backward compat)
            opportunity_ids: List of opportunity IDs
            granularity: 'combined', 'per_opp', or 'per_flw'
            audit_type: 'date_range', 'last_n_per_flw', or 'last_n_across_opp'
            start_date: Start date for date_range
            end_date: End date for date_range
            count: Count for last_n types
            flw_username: FLW username (for per_flw granularity) - not stored, just for notes
            opportunity_name: Opportunity name (for naming)
            audit_definition: Optional AuditTemplate to link to
            user: User object for auditor (if None, will look up from auditor_username)

        Returns:
            Created Audit instance
        """
        if self.dry_run:
            print(f"[DRY RUN] Would create audit for {granularity} / {audit_type}")
            return None

        # Get auditor User object
        if not user:
            try:
                user = User.objects.get(username=auditor_username)
            except User.DoesNotExist:
                # Fall back to first superuser
                user = User.objects.filter(is_superuser=True).first()
                if not user:
                    raise ValueError(f"User '{auditor_username}' not found and no superuser available")

        # Build audit notes
        notes = []
        if granularity == "combined":
            notes.append(f"Combined audit across {len(opportunity_ids)} opportunity(ies)")
        elif granularity == "per_opp":
            notes.append(f"Per-opportunity audit: {opportunity_name}")
        elif granularity == "per_flw":
            notes.append(f"Per-FLW audit: {flw_username}")

        if audit_type == "date_range":
            notes.append(f"Date range: {start_date} to {end_date}")
        elif audit_type == "last_n_per_flw":
            notes.append(f"Last {count} visits per FLW")
        elif audit_type == "last_n_across_opp":
            notes.append(f"Last {count} visits across opportunity")

        # Get primary opportunity for single-opportunity audits
        primary_opportunity = None
        if len(opportunity_ids) == 1:
            try:
                primary_opportunity = Opportunity.objects.get(id=opportunity_ids[0])
            except Opportunity.DoesNotExist:
                pass

        # Create audit
        audit = Audit.objects.create(
            auditor=user,
            primary_opportunity=primary_opportunity,
            opportunity_name=opportunity_name or f"{len(opportunity_ids)} opportunities",
            start_date=start_date or date.today(),
            end_date=end_date or date.today(),
            notes="\n".join(notes),
            status="in_progress",
            template=audit_definition,  # Link to template if provided
            title=audit_title,
            tag=audit_tag,
        )

        # NOTE: Visits must be assigned to the audit AFTER creation using audit.visits.set()
        # This is done in the audit_creator service after loading the visits

        return audit

    def _extract_question_id_for_attachment(self, form_data: dict, filename: str) -> str | None:
        """
        Extract the question ID that corresponds to an attachment filename.

        CommCare form data structure has question IDs as keys, and filenames as values
        for image/attachment questions. This recursively searches the form data to find
        the matching question ID.

        Args:
            form_data: The form data dict (typically form["form"])
            filename: The attachment filename to search for

        Returns:
            The question ID (key) if found, None otherwise
        """

        def search_dict(data, target_filename, path=""):
            """Recursively search for filename in nested dict structure"""
            if not isinstance(data, dict):
                return None

            for key, value in data.items():
                # Skip metadata and special fields
                if key in ["@xmlns", "@name", "@uiVersion", "@version", "meta", "#type"]:
                    continue

                current_path = f"{path}/{key}" if path else key

                # Check if this value matches our filename
                if isinstance(value, str) and value == target_filename:
                    return current_path

                # Recursively search nested dicts
                if isinstance(value, dict):
                    result = search_dict(value, target_filename, current_path)
                    if result:
                        return result

            return None

        # Try to find the filename in the form data
        form_content = form_data.get("form", {})
        question_path = search_dict(form_content, filename)

        return question_path

    def download_attachments(self, visits: list[UserVisit], progress_tracker=None):
        """
        Synchronously download attachments for visits from CommCare.

        This downloads images during audit creation so they're immediately available.

        Args:
            visits: List of UserVisit instances to download attachments for
            progress_tracker: Optional ProgressTracker for reporting progress
        """
        if self.dry_run:
            print(f"[DRY RUN] Would download attachments for {len(visits)} visits")
            return

        import os

        from django.core.files.base import ContentFile
        from django.core.files.storage import default_storage

        from commcare_connect.audit.management.extractors.commcare_extractor import CommCareExtractor
        from commcare_connect.opportunity.models import BlobMeta

        if not visits:
            return

        # Check if CommCare credentials are available
        if not os.getenv("COMMCARE_USERNAME") or not os.getenv("COMMCARE_API_KEY"):
            print("[WARNING]  CommCare credentials not configured (COMMCARE_USERNAME/COMMCARE_API_KEY)")
            print("[WARNING]  Skipping attachment downloads")
            return

        # Group visits by OPPORTUNITY, then determine the domain to use for each opportunity
        # (Don't use user domain - that's the user's personal link, not where forms are stored)
        from collections import Counter

        visits_by_opportunity = {}
        for visit in visits:
            opp_id = visit.opportunity_id
            if opp_id not in visits_by_opportunity:
                visits_by_opportunity[opp_id] = []
            visits_by_opportunity[opp_id].append(visit)

        # Build a map of all domains per opportunity for fallback
        # (one opportunity can span multiple CommCare domains)
        opportunity_domains = {}
        for opp_id, opp_visits in visits_by_opportunity.items():
            domains = [getattr(v, "_temp_cc_domain", None) for v in opp_visits]
            domain_counts = Counter([d for d in domains if d])
            if domain_counts:
                # Store all domains sorted by frequency
                opportunity_domains[opp_id] = [d for d, _ in domain_counts.most_common()]

        # Group visits by their primary (most common) domain
        visits_by_domain = {}
        skipped_visits = 0

        for opp_id, opp_visits in visits_by_opportunity.items():
            if opp_id not in opportunity_domains:
                skipped_visits += len(opp_visits)
                continue

            primary_domain = opportunity_domains[opp_id][0]
            if primary_domain not in visits_by_domain:
                visits_by_domain[primary_domain] = []
            # Store visits with opportunity context for fallback
            for v in opp_visits:
                v._opportunity_domains = opportunity_domains[opp_id]  # All domains to try
            visits_by_domain[primary_domain].extend(opp_visits)

        if skipped_visits > 0:
            print(f"[WARNING]  Skipped {skipped_visits} visits without domain information")
            print("[INFO]     Domain information is not available in Superset for these opportunities.")
            print(
                "[INFO]     To download attachments, the production database would need "
                "domain/app metadata configured."
            )

        total_downloaded = 0
        # Use the original input count as the total for progress reporting
        # (not the recalculated count after filtering, which may differ due to skipped visits)
        total_visits_input = len(visits)
        total_visits_to_process = sum(len(v) for v in visits_by_domain.values())
        visits_processed = 0

        print(
            f"[INFO] Will download attachments for {total_visits_to_process} visits "
            f"(out of {total_visits_input} total)"
        )

        # Process each domain
        for domain, domain_visits in visits_by_domain.items():
            try:
                print(f"[INFO] Downloading attachments for {len(domain_visits)} visits from {domain}...")
                extractor = CommCareExtractor(domain=domain)

                # CommCare API doesn't support batch fetching by form_ids
                # We need to fetch each form individually

                # Download attachments for each visit
                domain_downloaded = 0
                for idx, visit in enumerate(domain_visits, 1):
                    # Check for cancellation
                    if progress_tracker and progress_tracker.is_cancelled():
                        print("[INFO] Download cancelled by user")
                        extractor.close()
                        return

                    # Update progress
                    visits_processed += 1
                    if progress_tracker and (visits_processed % 5 == 0 or visits_processed == total_visits_to_process):
                        # Use total_visits_input (the number passed in) as the denominator
                        # This ensures the progress denominator matches what the user expects (e.g., sampled count)
                        progress_tracker.update(
                            visits_processed,
                            total_visits_input,
                            f"Downloading attachments ({visits_processed}/{total_visits_input} visits)...",
                            "downloading",
                            step_name="attachments",
                        )

                    # Fetch individual form from CommCare API
                    try:
                        if idx % 10 == 1 or idx == len(domain_visits):
                            import sys

                            print(
                                f"  Processing visit {idx}/{len(domain_visits)} (form: {visit.xform_id[:8]}...)",
                                flush=True,
                            )
                            sys.stdout.flush()

                        form_url = f"{extractor.base_url}/form/{visit.xform_id}/"
                        response = extractor.session.get(form_url, timeout=30)
                        response.raise_for_status()
                        form = response.json()
                    except Exception as e:
                        if idx % 10 == 1 or idx == len(domain_visits):
                            print(f"[WARNING]  Could not fetch form {visit.xform_id[:8]}...: {e}")
                        continue

                    attachments = form.get("attachments", {})
                    if not attachments:
                        continue

                    # Download each attachment
                    for filename, attachment_info in attachments.items():
                        if filename.endswith(".xml"):
                            continue

                        try:
                            # Extract URL
                            if isinstance(attachment_info, dict):
                                attachment_url = attachment_info.get("url")
                                content_type = attachment_info.get("content_type", "image/jpeg")
                            else:
                                attachment_url = attachment_info
                                content_type = "image/jpeg"

                            if not attachment_url:
                                continue

                            # Check if already exists
                            if BlobMeta.objects.filter(name=filename, parent_id=visit.xform_id).exists():
                                continue

                            # Extract question ID for this attachment
                            question_id = self._extract_question_id_for_attachment(form, filename)

                            # Download from CommCare
                            response = extractor.session.get(attachment_url, timeout=30)
                            response.raise_for_status()

                            # Create BlobMeta and save file
                            blob_meta = BlobMeta.objects.create(
                                name=filename,
                                parent_id=visit.xform_id,
                                content_length=len(response.content),
                                content_type=content_type,
                                question_id=question_id,
                            )

                            default_storage.save(str(blob_meta.blob_id), ContentFile(response.content, filename))
                            total_downloaded += 1
                            domain_downloaded += 1

                        except Exception as e:
                            print(f"[WARNING]  Error downloading {filename}: {e}")
                            continue

                print(f"  Downloaded {domain_downloaded} attachments from {domain}")
                extractor.close()

            except Exception as e:
                print(f"[WARNING]  Error processing domain {domain}: {e}")
                import traceback

                print(traceback.format_exc())
                continue

        if total_downloaded > 0:
            print(f"[OK] Downloaded {total_downloaded} attachments from {len(visits_by_domain)} domain(s)")
        else:
            print(
                f"[WARNING]  No attachments downloaded "
                f"(processed {len(visits_by_domain)} domains, {len(visits)} visits)"
            )
        self.stats["attachments_downloaded"] = total_downloaded

    def get_stats(self) -> dict:
        """Return statistics about loaded data."""
        return self.stats.copy()

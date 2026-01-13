"""
Connect API Facade

This facade provides a unified interface for accessing Connect data, whether from:
1. Superset warehouse (current implementation)
2. Production APIs (OAuth-based access to connect.dimagi.com)

The facade abstracts the data source and provides consistent methods for the audit workflow.
"""

import os
import tempfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import httpx
import pandas as pd
from django.conf import settings

from .superset_extractor import SupersetExtractor


@dataclass
class Program:
    """Program information"""

    id: int
    name: str
    slug: str
    description: str
    organization_id: int
    organization_name: str
    start_date: date
    end_date: date | None
    budget: int
    currency: str


@dataclass
class Opportunity:
    """Opportunity information"""

    id: int
    name: str
    description: str
    program_id: int | None
    program_name: str | None
    organization_id: int
    organization_name: str
    start_date: date
    end_date: date | None
    deliver_app_id: int | None
    deliver_app_domain: str | None
    deliver_app_cc_app_id: str | None
    is_test: bool
    active: bool
    visit_count: int = 0


@dataclass
class FieldWorker:
    """Field worker information"""

    id: int
    name: str
    email: str | None
    phone_number: str | None
    username: str | None
    opportunity_access_id: int | None
    last_active: datetime | None
    total_visits: int
    approved_visits: int
    pending_visits: int
    rejected_visits: int


@dataclass
class AuditParameters:
    """Parameters for audit session"""

    opportunity_id: int
    flw_ids: list[int]
    start_date: date
    end_date: date
    sample_size: int | None = None
    include_flagged_only: bool = False
    include_test_data: bool = False


class ConnectAPIFacade:
    """
    Unified facade for accessing Connect data from multiple sources.

    Supports both Superset warehouse and OAuth-based API access to connect.dimagi.com.
    """

    def __init__(self, user=None, request=None, use_production_api: bool = False):
        """
        Initialize the facade.

        Args:
            user: Django User object (for OAuth token retrieval). If provided, will attempt API access.
            request: HttpRequest object (required in labs mode to access session)
            use_production_api: Legacy parameter (deprecated in favor of user parameter)
        """
        self.oauth_token = None
        self.has_oauth_token = False
        self.production_url = None
        self.http_client = None
        self.superset_extractor = None
        self.commcare_app_fallback = None  # Lazy-loaded fallback data

        # Try to get OAuth token if user provided
        if user:
            from commcare_connect.audit.helpers import get_connect_oauth_token

            try:
                self.oauth_token = get_connect_oauth_token(user, request=request)
                if self.oauth_token:
                    self.has_oauth_token = True
                    self.production_url = settings.CONNECT_PRODUCTION_URL.rstrip("/")
                    # Initialize httpx client with Bearer token
                    self.http_client = httpx.Client(
                        headers={"Authorization": f"Bearer {self.oauth_token}"},
                        timeout=120.0,
                    )
            except Exception as e:
                print(f"[WARNING] Could not retrieve OAuth token: {e}")

        # Fall back to Superset if no OAuth token
        if not self.has_oauth_token:
            self.superset_extractor = SupersetExtractor()

    def authenticate(self) -> bool:
        """Authenticate with the data source."""
        if self.has_oauth_token:
            # Test OAuth token with the data export endpoint
            try:
                response = self.http_client.get(f"{self.production_url}/export/opp_org_program_list/")
                return response.status_code == 200
            except Exception as e:
                print(f"[ERROR] OAuth token authentication failed: {e}")
                return False
        else:
            return self.superset_extractor.authenticate()

    def _load_commcare_app_fallback(self) -> dict:
        """
        Load CommCareApp fallback data from CSV file.

        This is a temporary workaround because the opportunity_commcareapp table
        is not synced to Superset (0 rows). Once that table is populated in Superset,
        this fallback won't be needed.

        Returns:
            Dictionary mapping app_id -> {cc_domain, cc_app_id, name}
        """
        if self.commcare_app_fallback is not None:
            return self.commcare_app_fallback

        import csv
        from pathlib import Path

        self.commcare_app_fallback = {}

        # CSV file is in the audit app directory (../../ from this module)
        # This module is at: commcare_connect/audit/management/extractors/
        # CSV file is at: commcare_connect/audit/
        fallback_path = Path(__file__).parent.parent.parent / "commcare_app_fallback.csv"

        if not fallback_path.exists():
            return self.commcare_app_fallback

        try:
            with open(fallback_path) as f:
                reader = csv.DictReader(f)
                for row in reader:
                    app_id = int(row["id"])
                    self.commcare_app_fallback[app_id] = {
                        "cc_domain": row["cc_domain"],
                        "cc_app_id": row["cc_app_id"],
                        "name": row["name"],
                    }
        except Exception as e:
            print(f"[WARNING] Could not load CommCareApp fallback data: {e}")

        return self.commcare_app_fallback

    def _make_api_request(self, endpoint: str, method: str = "GET", stream: bool = False, **kwargs):
        """
        Make an API request to Connect production instance.

        Args:
            endpoint: API endpoint (e.g., "/export/opp_org_program_list/")
            method: HTTP method (GET, POST, etc.)
            stream: Whether to stream the response (for CSV downloads)
            **kwargs: Additional arguments to pass to httpx request

        Returns:
            For JSON responses: parsed JSON data
            For streaming responses: httpx Response object

        Raises:
            httpx.HTTPStatusError: If request fails
        """
        if not self.has_oauth_token:
            raise RuntimeError("No OAuth token available for API request")

        url = f"{self.production_url}{endpoint}"

        try:
            response = self.http_client.request(method, url, **kwargs)
            response.raise_for_status()

            if stream:
                return response
            else:
                # Parse JSON response
                return response.json()
        except httpx.HTTPStatusError as e:
            print(f"[ERROR] API request failed: {e}")
            print(f"[ERROR] Response: {e.response.text}")
            raise

    def search_programs(self, query: str = "", limit: int = 50) -> list[Program]:
        """
        Search programs by name or ID.

        Args:
            query: Search term (program name or ID)
            limit: Maximum number of results

        Returns:
            List of matching programs
        """
        if self.has_oauth_token:
            return self._search_programs_api(query, limit)
        else:
            return self._search_programs_superset(query, limit)

    def search_opportunities(self, query: str = "", limit: int = 50) -> list[Opportunity]:
        """
        Search opportunities by name or ID.

        Args:
            query: Search term (opportunity name or ID)
            limit: Maximum number of results

        Returns:
            List of matching opportunities
        """
        if self.has_oauth_token:
            return self._search_opportunities_api(query, limit)
        else:
            return self._search_opportunities_superset(query, limit)

    def get_opportunities_by_program(self, program_id: int) -> list[Opportunity]:
        """
        Get all opportunities for a specific program.

        Args:
            program_id: Program ID

        Returns:
            List of opportunities in the program
        """
        if self.has_oauth_token:
            return self._get_opportunities_by_program_api(program_id)
        else:
            return self._get_opportunities_by_program_superset(program_id)

    def get_field_workers_by_opportunity(self, opportunity_id: int) -> list[FieldWorker]:
        """
        Get all field workers associated with an opportunity.

        Args:
            opportunity_id: Opportunity ID

        Returns:
            List of field workers with visit statistics
        """
        if self.has_oauth_token:
            return self._get_field_workers_by_opportunity_api(opportunity_id)
        else:
            return self._get_field_workers_by_opportunity_superset(opportunity_id)

    def get_visit_date_range(self, opportunity_id: int) -> tuple[date, date]:
        """
        Get the date range of visits for an opportunity.

        Args:
            opportunity_id: Opportunity ID

        Returns:
            Tuple of (earliest_date, latest_date)
        """
        if self.has_oauth_token:
            return self._get_visit_date_range_api(opportunity_id)
        else:
            return self._get_visit_date_range_superset(opportunity_id)

    def get_visit_count_preview(self, params: AuditParameters) -> dict[str, int]:
        """
        Get a preview of how many visits would be included in the audit.

        Args:
            params: Audit parameters

        Returns:
            Dictionary with visit counts by status
        """
        if self.has_oauth_token:
            return self._get_visit_count_preview_api(params)
        else:
            return self._get_visit_count_preview_superset(params)

    def download_audit_data(self, params: AuditParameters, output_dir: str = "data") -> str:
        """
        Download all data needed for the audit session.

        Args:
            params: Audit parameters
            output_dir: Directory to save data files

        Returns:
            Path to the downloaded data file
        """
        if self.has_oauth_token:
            return self._download_audit_data_api(params, output_dir)
        else:
            return self._download_audit_data_superset(params, output_dir)

    # OAuth API implementation methods

    def _search_programs_api(self, query: str, limit: int) -> list[Program]:
        """Search programs using OAuth API."""
        # Call the API to get all programs/opportunities
        data = self._make_api_request("/export/opp_org_program_list/")

        programs_list = data.get("programs", [])
        results = []

        # Filter and convert to Program dataclasses
        query_lower = query.lower().strip()
        for prog_data in programs_list:
            # Filter by query (name or ID)
            if query_lower:
                prog_id_match = query_lower.isdigit() and int(query_lower) == prog_data.get("id")
                name_match = query_lower in prog_data.get("name", "").lower()
                if not (prog_id_match or name_match):
                    continue

            try:
                program = Program(
                    id=prog_data["id"],
                    name=prog_data["name"],
                    slug=str(prog_data["id"]),  # API doesn't expose slug directly
                    description="",  # API doesn't expose description
                    organization_id=0,  # Not directly available in this endpoint
                    organization_name=prog_data.get("organization", ""),
                    start_date=date.today(),  # Not available in this endpoint
                    end_date=None,
                    budget=0,  # Not available
                    currency=prog_data.get("currency", "USD"),
                )
                results.append(program)
            except (KeyError, ValueError) as e:
                print(f"[WARNING] Skipping program due to parse error: {e}")
                continue

            if len(results) >= limit:
                break

        return results

    def _search_opportunities_api(self, query: str, limit: int) -> list[Opportunity]:
        """Search opportunities using OAuth API."""
        # Call the API to get all programs/opportunities
        data = self._make_api_request("/export/opp_org_program_list/")

        opportunities_list = data.get("opportunities", [])
        results = []

        # Filter and convert to Opportunity dataclasses
        query_lower = query.lower().strip()
        for opp_data in opportunities_list:
            # Filter by query (name or ID)
            if query_lower:
                opp_id_match = query_lower.isdigit() and int(query_lower) == opp_data.get("id")
                name_match = query_lower in opp_data.get("name", "").lower()
                if not (opp_id_match or name_match):
                    continue

            try:
                opportunity = Opportunity(
                    id=opp_data["id"],
                    name=opp_data["name"],
                    description="",
                    program_id=opp_data.get("program"),
                    program_name=None,
                    organization_id=0,
                    organization_name=opp_data.get("organization", ""),
                    start_date=date.today(),
                    end_date=datetime.fromisoformat(opp_data["end_date"]).date() if opp_data.get("end_date") else None,
                    deliver_app_id=None,
                    deliver_app_domain=None,
                    deliver_app_cc_app_id=None,
                    is_test=False,
                    active=opp_data.get("is_active", True),
                    visit_count=0,
                )
                results.append(opportunity)
            except (KeyError, ValueError) as e:
                print(f"[WARNING] Skipping opportunity due to parse error: {e}")
                continue

            if len(results) >= limit:
                break

        return results

    def _get_opportunities_by_program_api(self, program_id: int) -> list[Opportunity]:
        """Get opportunities for a program using OAuth API."""
        # Call the API to get all programs/opportunities
        data = self._make_api_request("/export/opp_org_program_list/")

        opportunities_list = data.get("opportunities", [])
        results = []

        for opp_data in opportunities_list:
            if opp_data.get("program") != program_id:
                continue

            try:
                opportunity = Opportunity(
                    id=opp_data["id"],
                    name=opp_data["name"],
                    description="",
                    program_id=opp_data.get("program"),
                    program_name=None,
                    organization_id=0,
                    organization_name=opp_data.get("organization", ""),
                    start_date=date.today(),
                    end_date=datetime.fromisoformat(opp_data["end_date"]).date() if opp_data.get("end_date") else None,
                    deliver_app_id=None,
                    deliver_app_domain=None,
                    deliver_app_cc_app_id=None,
                    is_test=False,
                    active=opp_data.get("is_active", True),
                    visit_count=0,
                )
                results.append(opportunity)
            except (KeyError, ValueError) as e:
                print(f"[WARNING] Skipping opportunity due to parse error: {e}")
                continue

        return results

    def _get_field_workers_by_opportunity_api(self, opportunity_id: int) -> list[FieldWorker]:
        """Get field workers for an opportunity using OAuth API."""
        # Download user data CSV
        response = self._make_api_request(f"/export/opportunity/{opportunity_id}/user_data/", stream=True)

        # Write to temp file and parse
        with tempfile.NamedTemporaryFile(mode="wb", delete=False, suffix=".csv") as tmp_file:
            for chunk in response.iter_bytes():
                tmp_file.write(chunk)
            tmp_path = tmp_file.name

        try:
            df = pd.read_csv(tmp_path)
            results = []

            for _, row in df.iterrows():
                try:
                    # Try to get name from various possible columns (will be added in future PR)
                    name = row.get("name", row.get("display_name", row.get("full_name", "")))
                    if pd.notna(name) and name:
                        name = str(name).strip()
                    else:
                        name = ""  # Leave blank if not available

                    # Handle phone number - convert to string and remove leading +
                    phone = row.get("phone", row.get("phone_number", ""))
                    if pd.notna(phone):
                        phone_str = str(phone)
                        # Remove leading + if present
                        if phone_str.startswith("+"):
                            phone_str = phone_str[1:]
                        phone = phone_str
                    else:
                        phone = ""

                    # Get user_id if available (will be added in future PR)
                    user_id = 0
                    if "user_id" in row and pd.notna(row.get("user_id")):
                        user_id = int(row["user_id"])

                    flw = FieldWorker(
                        id=user_id,
                        name=name,
                        email=row.get("email", ""),
                        phone_number=phone,
                        username=row.get("username", ""),
                        opportunity_access_id=None,
                        last_active=pd.to_datetime(row["last_active"]) if pd.notna(row.get("last_active")) else None,
                        total_visits=int(row.get("total_visits", 0)) if pd.notna(row.get("total_visits")) else 0,
                        approved_visits=int(row.get("approved_visits", 0))
                        if pd.notna(row.get("approved_visits"))
                        else 0,
                        pending_visits=int(row.get("pending_visits", 0)) if pd.notna(row.get("pending_visits")) else 0,
                        rejected_visits=int(row.get("rejected_visits", 0))
                        if pd.notna(row.get("rejected_visits"))
                        else 0,
                    )
                    results.append(flw)
                except (KeyError, ValueError) as e:
                    print(f"[WARNING] Skipping FLW due to parse error: {e}")
                    continue

            return results
        finally:
            # Clean up temp file
            os.unlink(tmp_path)

    def _get_visit_date_range_api(self, opportunity_id: int) -> tuple[date, date]:
        """Get visit date range using OAuth API."""
        # Download a small sample of visits to get date range
        response = self._make_api_request(f"/export/opportunity/{opportunity_id}/user_visits/", stream=True)

        # Write to temp file and parse
        with tempfile.NamedTemporaryFile(mode="wb", delete=False, suffix=".csv") as tmp_file:
            for chunk in response.iter_bytes():
                tmp_file.write(chunk)
            tmp_path = tmp_file.name

        try:
            df = pd.read_csv(tmp_path)

            if df.empty:
                # Default to today if no visits
                return date.today(), date.today()

            # Parse visit_date column
            df["visit_date"] = pd.to_datetime(df["visit_date"])
            earliest = df["visit_date"].min().date()
            latest = df["visit_date"].max().date()

            return earliest, latest
        finally:
            # Clean up temp file
            os.unlink(tmp_path)

    def _get_visit_count_preview_api(self, params: AuditParameters) -> dict[str, int]:
        """Get visit count preview using OAuth API."""
        # Download visit data and count
        response = self._make_api_request(f"/export/opportunity/{params.opportunity_id}/user_visits/", stream=True)

        # Write to temp file and parse
        with tempfile.NamedTemporaryFile(mode="wb", delete=False, suffix=".csv") as tmp_file:
            for chunk in response.iter_bytes():
                tmp_file.write(chunk)
            tmp_path = tmp_file.name

        try:
            df = pd.read_csv(tmp_path)

            # Filter by FLW IDs
            if params.flw_ids:
                # Note: need to map username to user_id - this might require additional API call
                # For now, assuming username column exists
                pass

            # Filter by date range
            df["visit_date"] = pd.to_datetime(df["visit_date"]).dt.date
            df = df[(df["visit_date"] >= params.start_date) & (df["visit_date"] <= params.end_date)]

            # Count by status
            counts = {"total": len(df)}

            if "status" in df.columns:
                status_counts = df["status"].value_counts().to_dict()
                counts["approved"] = status_counts.get("approved", 0)
                counts["pending"] = status_counts.get("pending", 0)
                counts["rejected"] = status_counts.get("rejected", 0)

            return counts
        finally:
            # Clean up temp file
            os.unlink(tmp_path)

    def _download_audit_data_api(self, params: AuditParameters, output_dir: str) -> str:
        """Download audit data using OAuth API."""
        # Create output directory if needed
        Path(output_dir).mkdir(parents=True, exist_ok=True)

        # Download user visits
        response = self._make_api_request(f"/export/opportunity/{params.opportunity_id}/user_visits/", stream=True)

        # Generate output filename
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"audit_data_opp_{params.opportunity_id}_{timestamp}.csv"
        output_path = Path(output_dir) / filename

        # Stream to output file and apply filters
        with tempfile.NamedTemporaryFile(mode="wb", delete=False, suffix=".csv") as tmp_file:
            for chunk in response.iter_bytes():
                tmp_file.write(chunk)
            tmp_path = tmp_file.name

        try:
            # Read and filter
            df = pd.read_csv(tmp_path)

            # Apply filters
            df["visit_date"] = pd.to_datetime(df["visit_date"]).dt.date
            df = df[(df["visit_date"] >= params.start_date) & (df["visit_date"] <= params.end_date)]

            # Filter by status (approved only)
            if "status" in df.columns:
                df = df[df["status"] == "approved"]

            # Filter by FLW IDs if provided
            # Note: This requires mapping usernames - might need adjustment based on actual data

            # Apply sample size limit
            if params.sample_size and len(df) > params.sample_size:
                df = df.head(params.sample_size)

            # Write filtered data
            df.to_csv(output_path, index=False)

            print(f"[OK] Downloaded {len(df)} visits to {output_path}")
            print("[WARNING] Blob metadata not available via API - images will need to be fetched separately")

            return str(output_path)
        finally:
            # Clean up temp file
            os.unlink(tmp_path)

    # Superset implementation methods

    def _search_programs_superset(self, query: str, limit: int) -> list[Program]:
        """Search programs using Superset data."""
        sql = """
        SELECT DISTINCT
            p.id,
            p.name,
            p.slug,
            p.description,
            p.organization_id,
            o.name as organization_name,
            p.start_date,
            p.end_date,
            p.budget,
            p.currency
        FROM program_program p
        LEFT JOIN organization_organization o ON o.id = p.organization_id
        WHERE 1=1
        """

        if query.strip():
            # Check if query contains only digits and commas (multiple IDs)
            if all(c.isdigit() or c in ",. " for c in query.strip()):
                # Extract numeric IDs from the query
                id_parts = [part.strip() for part in query.replace(",", " ").split() if part.strip().isdigit()]
                if id_parts:
                    if len(id_parts) == 1:
                        sql += f" AND p.id = {id_parts[0]}"
                    else:
                        ids_str = ",".join(id_parts)
                        sql += f" AND p.id IN ({ids_str})"
            else:
                # Text search in name and description
                escaped_query = query.replace("'", "''")  # Basic SQL injection protection
                sql += f" AND (p.name ILIKE '%{escaped_query}%' OR p.description ILIKE '%{escaped_query}%')"

        sql += " ORDER BY p.name"

        df = self.superset_extractor.execute_query(sql)
        if df is None or df.empty:
            return []

        programs = []
        for _, row in df.iterrows():
            programs.append(
                Program(
                    id=row["id"],
                    name=row["name"],
                    slug=row["slug"],
                    description=row["description"] or "",
                    organization_id=row["organization_id"],
                    organization_name=row["organization_name"] or "",
                    start_date=row["start_date"],
                    end_date=row["end_date"],
                    budget=row["budget"],
                    currency=row["currency"],
                )
            )

        return programs

    def _search_opportunities_superset(self, query: str, limit: int) -> list[Opportunity]:
        """Search opportunities using Superset data."""
        sql = """
        SELECT DISTINCT
            o.id,
            o.name,
            o.description,
            mo.program_id,
            p.name as program_name,
            o.organization_id,
            org.name as organization_name,
            o.start_date,
            o.end_date,
            o.deliver_app_id,
            da.cc_domain as deliver_app_domain,
            da.cc_app_id as deliver_app_cc_app_id,
            o.is_test,
            o.active,
            COALESCE(visit_counts.visit_count, 0) as visit_count
        FROM opportunity_opportunity o
        LEFT JOIN program_managedopportunity mo ON mo.opportunity_ptr_id = o.id
        LEFT JOIN program_program p ON p.id = mo.program_id
        LEFT JOIN organization_organization org ON org.id = o.organization_id
        LEFT JOIN opportunity_commcareapp da ON da.id = o.deliver_app_id
        LEFT JOIN (
            SELECT
                uv.opportunity_id,
                COUNT(*) as visit_count
            FROM opportunity_uservisit uv
            GROUP BY uv.opportunity_id
        ) visit_counts ON visit_counts.opportunity_id = o.id
        WHERE 1=1
        """

        if query.strip():
            # Check if query contains only digits and commas (multiple IDs)
            if all(c.isdigit() or c in ",. " for c in query.strip()):
                # Extract numeric IDs from the query
                id_parts = [part.strip() for part in query.replace(",", " ").split() if part.strip().isdigit()]
                if id_parts:
                    if len(id_parts) == 1:
                        sql += f" AND o.id = {id_parts[0]}"
                    else:
                        ids_str = ",".join(id_parts)
                        sql += f" AND o.id IN ({ids_str})"
            else:
                # Text search in name and description
                escaped_query = query.replace("'", "''")  # Basic SQL injection protection
                sql += f" AND (o.name ILIKE '%{escaped_query}%' OR o.description ILIKE '%{escaped_query}%')"

        sql += " ORDER BY COALESCE(visit_counts.visit_count, 0) DESC, o.name"

        df = self.superset_extractor.execute_query(sql)
        if df is None or df.empty:
            return []

        opportunities = []
        for _, row in df.iterrows():
            # Convert date strings to date objects
            start_date = None
            if row["start_date"] and str(row["start_date"]) != "None":
                from datetime import datetime

                if isinstance(row["start_date"], str):
                    start_date = datetime.strptime(row["start_date"], "%Y-%m-%d").date()
                else:
                    start_date = row["start_date"]

            end_date = None
            if row["end_date"] and str(row["end_date"]) != "None":
                from datetime import datetime

                if isinstance(row["end_date"], str):
                    end_date = datetime.strptime(row["end_date"], "%Y-%m-%d").date()
                else:
                    end_date = row["end_date"]

            # Handle visit_count more carefully - could be NaN, None, or numeric
            visit_count = 0
            try:
                import pandas as pd

                if "visit_count" in row:
                    vc = row["visit_count"]
                    if pd.notna(vc):  # Check if not NaN/None
                        visit_count = int(vc)
            except Exception:
                visit_count = 0

            opportunities.append(
                Opportunity(
                    id=row["id"],
                    name=row["name"],
                    description=row["description"] or "",
                    program_id=row["program_id"],
                    program_name=row["program_name"],
                    organization_id=row["organization_id"],
                    organization_name=row["organization_name"] or "",
                    start_date=start_date,
                    end_date=end_date,
                    deliver_app_id=row["deliver_app_id"],
                    deliver_app_domain=row["deliver_app_domain"],
                    deliver_app_cc_app_id=row["deliver_app_cc_app_id"],
                    is_test=row["is_test"],
                    active=row["active"],
                    visit_count=visit_count,
                )
            )

        return opportunities

    def _get_opportunities_by_program_superset(self, program_id: int) -> list[Opportunity]:
        """Get opportunities by program using Superset data."""
        sql = f"""
        SELECT DISTINCT
            o.id,
            o.name,
            o.description,
            mo.program_id,
            p.name as program_name,
            o.organization_id,
            org.name as organization_name,
            o.start_date,
            o.end_date,
            o.deliver_app_id,
            da.cc_domain as deliver_app_domain,
            da.cc_app_id as deliver_app_cc_app_id,
            o.is_test,
            o.active
        FROM opportunity_opportunity o
        LEFT JOIN program_managedopportunity mo ON mo.opportunity_ptr_id = o.id
        LEFT JOIN program_program p ON p.id = mo.program_id
        LEFT JOIN organization_organization org ON org.id = o.organization_id
        LEFT JOIN opportunity_commcareapp da ON da.id = o.deliver_app_id
        WHERE mo.program_id = {program_id}
        ORDER BY o.name
        """

        df = self.superset_extractor.execute_query(sql)
        if df is None or df.empty:
            return []

        opportunities = []
        for _, row in df.iterrows():
            opportunities.append(
                Opportunity(
                    id=row["id"],
                    name=row["name"],
                    description=row["description"] or "",
                    program_id=row["program_id"],
                    program_name=row["program_name"],
                    organization_id=row["organization_id"],
                    organization_name=row["organization_name"] or "",
                    start_date=row["start_date"],
                    end_date=row["end_date"],
                    deliver_app_id=row["deliver_app_id"],
                    deliver_app_domain=row["deliver_app_domain"],
                    deliver_app_cc_app_id=row["deliver_app_cc_app_id"],
                    is_test=row["is_test"],
                    active=row["active"],
                )
            )

        return opportunities

    def _get_field_workers_by_opportunity_superset(self, opportunity_id: int) -> list[FieldWorker]:
        """Get field workers by opportunity using Superset data."""
        sql = f"""
        SELECT
            u.id,
            u.name,
            u.email,
            u.phone_number,
            u.username,
            oa.id as opportunity_access_id,
            oa.last_active,
            COUNT(uv.id) as total_visits,
            COUNT(CASE WHEN uv.status = 'approved' THEN 1 END) as approved_visits,
            COUNT(CASE WHEN uv.status = 'pending' THEN 1 END) as pending_visits,
            COUNT(CASE WHEN uv.status = 'rejected' THEN 1 END) as rejected_visits
        FROM users_user u
        INNER JOIN opportunity_opportunityaccess oa ON oa.user_id = u.id
        LEFT JOIN opportunity_uservisit uv ON uv.user_id = u.id AND uv.opportunity_id = {opportunity_id}
        WHERE oa.opportunity_id = {opportunity_id}
            AND oa.accepted = true
            AND oa.suspended = false
        GROUP BY u.id, u.name, u.email, u.phone_number, u.username, oa.id, oa.last_active
        ORDER BY u.name
        """

        df = self.superset_extractor.execute_query(sql)
        if df is None or df.empty:
            return []

        field_workers = []
        for _, row in df.iterrows():
            field_workers.append(
                FieldWorker(
                    id=row["id"],
                    name=row["name"],
                    email=row["email"],
                    phone_number=row["phone_number"],
                    username=row["username"],
                    opportunity_access_id=row["opportunity_access_id"],
                    last_active=row["last_active"],
                    total_visits=row["total_visits"],
                    approved_visits=row["approved_visits"],
                    pending_visits=row["pending_visits"],
                    rejected_visits=row["rejected_visits"],
                )
            )

        return field_workers

    def _get_visit_date_range_superset(self, opportunity_id: int) -> tuple[date, date]:
        """Get visit date range using Superset data."""
        sql = f"""
        SELECT
            MIN(visit_date::date) as earliest_date,
            MAX(visit_date::date) as latest_date
        FROM opportunity_uservisit
        WHERE opportunity_id = {opportunity_id}
        """

        df = self.superset_extractor.execute_query(sql)
        if df is None or df.empty:
            # Return reasonable defaults
            today = date.today()
            return today, today

        row = df.iloc[0]
        return row["earliest_date"], row["latest_date"]

    def _get_visit_count_preview_superset(self, params: AuditParameters) -> dict[str, int]:
        """Get visit count preview using Superset data."""
        flw_ids_str = ",".join(map(str, params.flw_ids))

        sql = f"""
        SELECT
            status,
            COUNT(*) as count
        FROM opportunity_uservisit
        WHERE opportunity_id = {params.opportunity_id}
            AND user_id IN ({flw_ids_str})
            AND visit_date::date >= '{params.start_date}'
            AND visit_date::date <= '{params.end_date}'
        """

        if not params.include_test_data:
            sql += " AND opportunity_id NOT IN (SELECT id FROM opportunity_opportunity WHERE is_test = true)"

        if params.include_flagged_only:
            sql += " AND flagged = true"

        sql += " GROUP BY status"

        df = self.superset_extractor.execute_query(sql)
        if df is None or df.empty:
            return {"total": 0}

        counts = {"total": 0}
        for _, row in df.iterrows():
            counts[row["status"]] = row["count"]
            counts["total"] += row["count"]

        return counts

    def _download_audit_data_superset(self, params: AuditParameters, output_dir: str) -> str:
        """Download audit data using Superset."""
        flw_ids_str = ",".join(map(str, params.flw_ids))

        # Build comprehensive query for audit data
        sql = f"""
        SELECT
            uv.id as visit_id,
            uv.xform_id,
            uv.visit_date,
            uv.user_id,
            u.name as user_name,
            uv.opportunity_id,
            o.name as opportunity_name,
            uv.status,
            uv.deliver_unit_id,
            du.name as deliver_unit_name,
            uv.entity_id,
            uv.entity_name,
            uv.location,
            uv.flagged,
            uv.flag_reason,
            uv.form_json,
            -- Blob metadata for images
            bm.blob_id,
            bm.name as blob_name,
            bm.content_type,
            bm.content_length
        FROM opportunity_uservisit uv
        INNER JOIN users_user u ON u.id = uv.user_id
        INNER JOIN opportunity_opportunity o ON o.id = uv.opportunity_id
        LEFT JOIN opportunity_deliverunit du ON du.id = uv.deliver_unit_id
        LEFT JOIN opportunity_blobmeta bm ON bm.parent_id = uv.xform_id
        WHERE uv.opportunity_id = {params.opportunity_id}
            AND uv.user_id IN ({flw_ids_str})
            AND uv.visit_date::date >= '{params.start_date}'
            AND uv.visit_date::date <= '{params.end_date}'
            AND uv.status = 'approved'
        """

        if not params.include_test_data:
            sql += " AND o.is_test = false"

        if params.include_flagged_only:
            sql += " AND uv.flagged = true"

        sql += " ORDER BY uv.visit_date, uv.user_id"

        if params.sample_size:
            sql += f" LIMIT {params.sample_size}"

        # Generate output filename
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"audit_data_opp_{params.opportunity_id}_{timestamp}.csv"
        output_path = Path(output_dir) / filename

        # Ensure output directory exists
        output_path.parent.mkdir(exist_ok=True)

        # Execute query and save to file
        result = self.superset_extractor.execute_query(sql_query=sql, output_file=str(output_path), verbose=True)

        if result is not None:
            return str(output_path)
        else:
            raise Exception("Failed to download audit data")

    def get_flw_visit_counts_by_date_range(self, opportunity_id: int, start_date: str, end_date: str) -> dict:
        """Get FLW and visit counts for an opportunity within a date range."""
        # First get summary counts
        summary_sql = f"""
        SELECT
            COUNT(DISTINCT uv.user_id) as total_flws,
            COUNT(*) as total_visits,
            MIN(uv.visit_date) as earliest_visit,
            MAX(uv.visit_date) as latest_visit
        FROM opportunity_uservisit uv
        WHERE uv.opportunity_id = {opportunity_id}
        AND uv.visit_date::date >= '{start_date}'
        AND uv.visit_date::date <= '{end_date}'
        """

        df = self.superset_extractor.execute_query(summary_sql)
        if df is None or df.empty:
            return {"total_flws": 0, "total_visits": 0, "date_range": f"{start_date} to {end_date}", "flws": []}

        row = df.iloc[0]

        # Get detailed FLW information
        flw_sql = f"""
        SELECT
            uv.user_id,
            u.name as flw_name,
            u.username,
            COUNT(*) as visit_count,
            MIN(uv.visit_date) as earliest_visit,
            MAX(uv.visit_date) as latest_visit
        FROM opportunity_uservisit uv
        LEFT JOIN users_user u ON u.id = uv.user_id
        WHERE uv.opportunity_id = {opportunity_id}
        AND uv.visit_date::date >= '{start_date}'
        AND uv.visit_date::date <= '{end_date}'
        GROUP BY uv.user_id, u.name, u.username
        ORDER BY visit_count DESC, u.name
        """

        flw_df = self.superset_extractor.execute_query(flw_sql)
        flws = []
        if flw_df is not None and not flw_df.empty:
            for _, flw_row in flw_df.iterrows():
                flws.append(
                    {
                        "connect_id": flw_row.get("username")
                        or flw_row.get("connect_id")
                        or f"user_{flw_row['user_id']}",
                        "name": flw_row["flw_name"] or f"User {flw_row['user_id']}",
                        "visit_count": int(flw_row["visit_count"]),
                        "earliest_visit": flw_row["earliest_visit"],
                        "latest_visit": flw_row["latest_visit"],
                    }
                )

        return {
            "total_flws": int(row["total_flws"]) if row["total_flws"] else 0,
            "total_visits": int(row["total_visits"]) if row["total_visits"] else 0,
            "date_range": f"{start_date} to {end_date}",
            "flws": flws,
        }

    def get_flw_visit_counts_last_n_per_flw(self, opportunity_id: int, n_per_flw: int) -> dict:
        """Get FLW and visit counts for the last N visits per FLW."""
        # First get summary counts
        summary_sql = f"""
        WITH ranked_visits AS (
            SELECT
                uv.user_id,
                uv.visit_date,
                ROW_NUMBER() OVER (PARTITION BY uv.user_id ORDER BY uv.visit_date DESC) as rn
            FROM opportunity_uservisit uv
            WHERE uv.opportunity_id = {opportunity_id}
        ),
        filtered_visits AS (
            SELECT * FROM ranked_visits WHERE rn <= {n_per_flw}
        )
        SELECT
            COUNT(DISTINCT user_id) as total_flws,
            COUNT(*) as total_visits,
            MIN(visit_date) as earliest_visit,
            MAX(visit_date) as latest_visit
        FROM filtered_visits
        """

        df = self.superset_extractor.execute_query(summary_sql)
        if df is None or df.empty:
            return {"total_flws": 0, "total_visits": 0, "date_range": f"Last {n_per_flw} visits per FLW", "flws": []}

        row = df.iloc[0]
        earliest = row["earliest_visit"]
        latest = row["latest_visit"]
        date_range = f"Last {n_per_flw} visits per FLW"
        if earliest and latest:
            # Handle both datetime objects and strings
            if hasattr(earliest, "strftime"):
                earliest_str = earliest.strftime("%Y-%m-%d")
            else:
                earliest_str = str(earliest)[:10]  # Take first 10 chars for date

            if hasattr(latest, "strftime"):
                latest_str = latest.strftime("%Y-%m-%d")
            else:
                latest_str = str(latest)[:10]  # Take first 10 chars for date

            date_range += f" ({earliest_str} to {latest_str})"

        # Get detailed FLW information
        flw_sql = f"""
        WITH ranked_visits AS (
            SELECT
                uv.user_id,
                uv.visit_date,
                ROW_NUMBER() OVER (PARTITION BY uv.user_id ORDER BY uv.visit_date DESC) as rn
            FROM opportunity_uservisit uv
            WHERE uv.opportunity_id = {opportunity_id}
        ),
        filtered_visits AS (
            SELECT * FROM ranked_visits WHERE rn <= {n_per_flw}
        )
        SELECT
            fv.user_id,
            u.name as flw_name,
            u.username,
            COUNT(*) as visit_count,
            MIN(fv.visit_date) as earliest_visit,
            MAX(fv.visit_date) as latest_visit
        FROM filtered_visits fv
        LEFT JOIN users_user u ON u.id = fv.user_id
        GROUP BY fv.user_id, u.name, u.username
        ORDER BY visit_count DESC, u.name
        """

        flw_df = self.superset_extractor.execute_query(flw_sql)
        flws = []
        if flw_df is not None and not flw_df.empty:
            for _, flw_row in flw_df.iterrows():
                flws.append(
                    {
                        "connect_id": flw_row.get("username")
                        or flw_row.get("connect_id")
                        or f"user_{flw_row['user_id']}",
                        "name": flw_row["flw_name"] or f"User {flw_row['user_id']}",
                        "visit_count": int(flw_row["visit_count"]),
                        "earliest_visit": flw_row["earliest_visit"],
                        "latest_visit": flw_row["latest_visit"],
                    }
                )

        return {
            "total_flws": int(row["total_flws"]) if row["total_flws"] else 0,
            "total_visits": int(row["total_visits"]) if row["total_visits"] else 0,
            "date_range": date_range,
            "flws": flws,
        }

    def get_flw_visit_counts_last_n_across_opp(self, opportunity_id: int, n_total: int) -> dict:
        """Get FLW and visit counts for the last N visits across the entire opportunity."""
        # First get summary counts
        summary_sql = f"""
        WITH recent_visits AS (
            SELECT
                uv.user_id,
                uv.visit_date
            FROM opportunity_uservisit uv
            WHERE uv.opportunity_id = {opportunity_id}
            ORDER BY uv.visit_date DESC
            LIMIT {n_total}
        )
        SELECT
            COUNT(DISTINCT user_id) as total_flws,
            COUNT(*) as total_visits,
            MIN(visit_date) as earliest_visit,
            MAX(visit_date) as latest_visit
        FROM recent_visits
        """

        df = self.superset_extractor.execute_query(summary_sql)
        if df is None or df.empty:
            return {
                "total_flws": 0,
                "total_visits": 0,
                "date_range": f"Last {n_total} visits across opportunity",
                "flws": [],
            }

        row = df.iloc[0]
        earliest = row["earliest_visit"]
        latest = row["latest_visit"]
        date_range = f"Last {n_total} visits across opportunity"
        if earliest and latest:
            # Handle both datetime objects and strings
            if hasattr(earliest, "strftime"):
                earliest_str = earliest.strftime("%Y-%m-%d")
            else:
                earliest_str = str(earliest)[:10]  # Take first 10 chars for date

            if hasattr(latest, "strftime"):
                latest_str = latest.strftime("%Y-%m-%d")
            else:
                latest_str = str(latest)[:10]  # Take first 10 chars for date

            date_range += f" ({earliest_str} to {latest_str})"

        # Get detailed FLW information
        flw_sql = f"""
        WITH recent_visits AS (
            SELECT
                uv.user_id,
                uv.visit_date
            FROM opportunity_uservisit uv
            WHERE uv.opportunity_id = {opportunity_id}
            ORDER BY uv.visit_date DESC
            LIMIT {n_total}
        )
        SELECT
            rv.user_id,
            u.name as flw_name,
            u.username,
            COUNT(*) as visit_count,
            MIN(rv.visit_date) as earliest_visit,
            MAX(rv.visit_date) as latest_visit
        FROM recent_visits rv
        LEFT JOIN users_user u ON u.id = rv.user_id
        GROUP BY rv.user_id, u.name, u.username
        ORDER BY visit_count DESC, u.name
        """

        flw_df = self.superset_extractor.execute_query(flw_sql)
        flws = []
        if flw_df is not None and not flw_df.empty:
            for _, flw_row in flw_df.iterrows():
                flws.append(
                    {
                        "connect_id": flw_row.get("username")
                        or flw_row.get("connect_id")
                        or f"user_{flw_row['user_id']}",
                        "name": flw_row["flw_name"] or f"User {flw_row['user_id']}",
                        "visit_count": int(flw_row["visit_count"]),
                        "earliest_visit": flw_row["earliest_visit"],
                        "latest_visit": flw_row["latest_visit"],
                    }
                )

        return {
            "total_flws": int(row["total_flws"]) if row["total_flws"] else 0,
            "total_visits": int(row["total_visits"]) if row["total_visits"] else 0,
            "date_range": date_range,
            "flws": flws,
        }

    def get_opportunity_details(self, opportunity_ids: list[int]) -> list[dict]:
        """
        Get full opportunity objects from Superset for data loading.

        Args:
            opportunity_ids: List of opportunity IDs to fetch

        Returns:
            List of opportunity dictionaries with all fields
        """
        if not opportunity_ids:
            return []

        ids_str = ",".join(str(id) for id in opportunity_ids)

        sql = f"""
        SELECT
            o.id,
            o.name,
            o.description,
            o.start_date,
            o.end_date,
            o.is_test,
            o.active,
            o.organization_id,
            o.deliver_app_id,
            mo.program_id,
            p.name as program_name,
            org.name as organization_name
        FROM opportunity_opportunity o
        LEFT JOIN program_managedopportunity mo ON mo.opportunity_ptr_id = o.id
        LEFT JOIN program_program p ON p.id = mo.program_id
        LEFT JOIN organization_organization org ON org.id = o.organization_id
        WHERE o.id IN ({ids_str})
        """

        df = self.superset_extractor.execute_query(sql)
        if df is None or df.empty:
            print(f"[ERROR] No opportunity data found for IDs: {ids_str}")
            return []

        # Convert DataFrame to list of dictionaries
        opportunities = []
        for _, row in df.iterrows():
            opp_dict = row.to_dict()
            # Convert NaT/NaN to None for JSON serialization
            for key, value in opp_dict.items():
                if hasattr(value, "__class__") and "NaTType" in str(value.__class__):
                    opp_dict[key] = None
            opportunities.append(opp_dict)

        return opportunities

    def get_program_details(self, opportunity_ids: list[int]) -> list[dict]:
        """
        Get program objects for selected opportunities.

        Args:
            opportunity_ids: List of opportunity IDs

        Returns:
            List of unique program dictionaries
        """
        if not opportunity_ids:
            return []

        ids_str = ",".join(str(id) for id in opportunity_ids)

        sql = f"""
        SELECT DISTINCT
            p.id,
            p.name,
            p.slug,
            p.description,
            p.organization_id,
            p.start_date,
            p.end_date,
            p.budget,
            p.currency,
            org.name as organization_name
        FROM program_program p
        JOIN program_managedopportunity mo ON mo.program_id = p.id
        LEFT JOIN users_organization org ON org.id = p.organization_id
        WHERE mo.opportunity_ptr_id IN ({ids_str})
        """

        df = self.superset_extractor.execute_query(sql)
        if df is None or df.empty:
            return []

        programs = []
        for _, row in df.iterrows():
            prog_dict = row.to_dict()
            # Convert NaT/NaN to None
            for key, value in prog_dict.items():
                if hasattr(value, "__class__") and "NaTType" in str(value.__class__):
                    prog_dict[key] = None
            programs.append(prog_dict)

        return programs

    def get_users_for_opportunities(self, opportunity_ids: list[int]) -> list[dict]:
        """
        Get User records for FLWs who have visits in these opportunities.

        Args:
            opportunity_ids: List of opportunity IDs

        Returns:
            List of user dictionaries
        """
        if not opportunity_ids:
            return []

        ids_str = ",".join(str(id) for id in opportunity_ids)

        sql = f"""
        SELECT DISTINCT
            u.id,
            u.username,
            u.name,
            u.email,
            u.phone_number,
            u.date_joined,
            u.last_login,
            u.is_active
        FROM users_user u
        JOIN opportunity_uservisit uv ON uv.user_id = u.id
        WHERE uv.opportunity_id IN ({ids_str})
        ORDER BY u.username
        """

        df = self.superset_extractor.execute_query(sql)
        if df is None or df.empty:
            return []

        users = []
        for _, row in df.iterrows():
            user_dict = row.to_dict()
            # Convert NaT/NaN to None
            for key, value in user_dict.items():
                if hasattr(value, "__class__") and "NaTType" in str(value.__class__):
                    user_dict[key] = None
            users.append(user_dict)

        return users

    def get_unique_flws_across_opportunities(self, opportunity_ids: list[int]) -> list[dict]:
        """
        Get unique FLW list across all selected opportunities for 'per_flw' granularity.

        Args:
            opportunity_ids: List of opportunity IDs

        Returns:
            List of FLW dictionaries with their opportunity associations
        """
        if not opportunity_ids:
            return []

        ids_str = ",".join(str(id) for id in opportunity_ids)

        sql = f"""
        SELECT
            u.id as user_id,
            u.username,
            u.name,
            COUNT(DISTINCT uv.opportunity_id) as opp_count,
            COUNT(uv.id) as total_visits,
            array_agg(DISTINCT o.name) as opportunity_names,
            array_agg(DISTINCT uv.opportunity_id) as opportunity_ids
        FROM users_user u
        JOIN opportunity_uservisit uv ON uv.user_id = u.id
        JOIN opportunity_opportunity o ON o.id = uv.opportunity_id
        WHERE uv.opportunity_id IN ({ids_str})
          AND uv.status = 'approved'
        GROUP BY u.id, u.username, u.name
        ORDER BY u.name
        """

        df = self.superset_extractor.execute_query(sql)
        if df is None or df.empty:
            return []

        flws = []
        for _, row in df.iterrows():
            # Handle Postgres array_agg results which may come as strings like "{1,2,3}" or "[1,2,3]"
            opp_names = row["opportunity_names"]
            if isinstance(opp_names, str):
                # Remove curly/square braces and split
                opp_names = (
                    opp_names.strip("{}[]").split(",") if opp_names and opp_names not in ["{}", "[]", ""] else []
                )
                # Strip quotes from each name
                opp_names = [name.strip().strip('"').strip("'") for name in opp_names if name.strip()]
            elif not opp_names:
                opp_names = []

            opp_ids = row["opportunity_ids"]
            if isinstance(opp_ids, str):
                # Remove curly/square braces and split, then convert to ints
                opp_ids_str = opp_ids.strip("{}[]")
                if opp_ids_str and opp_ids not in ["{}", "[]", ""]:
                    opp_ids = [int(oid.strip().strip('"').strip("'")) for oid in opp_ids_str.split(",") if oid.strip()]
                else:
                    opp_ids = []
            elif not opp_ids:
                opp_ids = []
            else:
                opp_ids = [int(oid) for oid in opp_ids]

            flw_dict = {
                "user_id": int(row["user_id"]),
                "username": row["username"],
                "name": row["name"],
                "opp_count": int(row["opp_count"]),
                "total_visits": int(row["total_visits"]),
                "opportunity_names": opp_names,
                "opportunity_ids": opp_ids,
            }
            flws.append(flw_dict)

        return flws

    def get_deliver_units_for_visits(self, opportunity_ids: list[int]) -> list[dict]:
        """
        Get DeliverUnit records (with their CommCareApp and HQServer) for visits in given opportunities.

        Args:
            opportunity_ids: List of opportunity IDs

        Returns:
            List of deliver unit dictionaries with nested app and server data
        """
        if not opportunity_ids:
            return []

        ids_str = ",".join(str(id) for id in opportunity_ids)

        sql = f"""
        SELECT DISTINCT
            du.id,
            du.name,
            du.slug,
            du.description,
            du.optional,
            du.app_id,
            app.cc_app_id,
            app.name as app_name,
            app.cc_domain,
            app.hq_server_id,
            hs.name as hq_server_name,
            hs.url as hq_server_url
        FROM opportunity_deliverunit du
        INNER JOIN opportunity_uservisit uv ON uv.deliver_unit_id = du.id
        LEFT JOIN opportunity_commcareapp app ON app.id = du.app_id
        LEFT JOIN commcarehq_hqserver hs ON hs.id = app.hq_server_id
        WHERE uv.opportunity_id IN ({ids_str})
            AND uv.status = 'approved'
        ORDER BY du.id
        """

        df = self.superset_extractor.execute_query(sql)
        if df is None or df.empty:
            return []

        import pandas as pd

        deliver_units = []
        for _, row in df.iterrows():
            du_dict = row.to_dict()
            # Convert NaT/NaN to None
            for key, value in du_dict.items():
                if pd.isna(value):
                    du_dict[key] = None
            deliver_units.append(du_dict)

        return deliver_units

    def get_user_visits_for_audit(
        self,
        opportunity_ids: list[int],
        audit_type: str,
        start_date: date = None,
        end_date: date = None,
        count: int = None,
        user_id: int = None,
    ) -> list[dict]:
        """
        Get UserVisit records based on audit criteria.

        Args:
            opportunity_ids: List of opportunity IDs
            audit_type: 'date_range', 'last_n_per_flw', 'last_n_per_opp', or 'last_n_across_all'
            start_date: Start date for date_range type
            end_date: End date for date_range type
            count: Number of visits for last_n types
            user_id: Optional filter for specific user (for per_flw granularity)

        Returns:
            List of visit dictionaries
        """
        if not opportunity_ids:
            return []

        ids_str = ",".join(str(id) for id in opportunity_ids)

        # Base SELECT clause (skip form_json - expensive download)
        # IMPORTANT: cc_domain comes from opportunity_commcareapp table, but that table
        # is currently EMPTY in Superset (0 rows). Until CommCareApp records are synced
        # to Superset, cc_domain will be NULL and attachments cannot be downloaded.
        # Get domain from deliver_unit app (preferred) or opportunity's deliver_app (fallback)
        select_clause = """
        SELECT
            uv.id,
            uv.xform_id,
            uv.user_id,
            uv.opportunity_id,
            uv.visit_date,
            uv.entity_id,
            uv.entity_name,
            uv.location,
            uv.status,
            uv.reason,
            uv.flag_reason,
            uv.flagged,
            uv.deliver_unit_id,
            u.username,
            u.name as user_name,
            COALESCE(unit_app.cc_domain, opp_app.cc_domain) as cc_domain,
            COALESCE(unit_app.cc_app_id, opp_app.cc_app_id) as cc_app_id
        FROM opportunity_uservisit uv
        LEFT JOIN users_user u ON u.id = uv.user_id
        LEFT JOIN opportunity_opportunity o ON o.id = uv.opportunity_id
        LEFT JOIN opportunity_deliverunit du ON du.id = uv.deliver_unit_id
        LEFT JOIN opportunity_commcareapp unit_app ON unit_app.id = du.app_id
        LEFT JOIN opportunity_commcareapp opp_app ON opp_app.id = o.deliver_app_id
        """

        # Build WHERE clause based on audit type
        where_conditions = [f"uv.opportunity_id IN ({ids_str})", "uv.status = 'approved'"]

        if user_id:
            where_conditions.append(f"uv.user_id = {user_id}")

        if audit_type == "date_range":
            if start_date and end_date:
                where_conditions.append(f"DATE(uv.visit_date) >= '{start_date}'")
                where_conditions.append(f"DATE(uv.visit_date) <= '{end_date}'")
            sql = f"""
            {select_clause}
            WHERE {" AND ".join(where_conditions)}
            ORDER BY uv.visit_date DESC
            """

        elif audit_type == "last_n_per_flw":
            # Get last N visits per FLW
            select_with_rank = select_clause.replace(
                "SELECT", "SELECT ROW_NUMBER() OVER (PARTITION BY uv.user_id ORDER BY uv.visit_date DESC) as rank,"
            )
            sql = f"""
            WITH ranked_visits AS (
                {select_with_rank}
                WHERE {" AND ".join(where_conditions)}
            )
            SELECT *
            FROM ranked_visits
            WHERE rank <= {count}
            ORDER BY user_id, visit_date DESC
            """

        elif audit_type == "last_n_per_opp":
            # Get last N visits per opportunity (limit applies per opportunity, not globally)
            # NOTE: This is handled at the caller level by calling this method once per opportunity
            # Use CTE to apply LIMIT in inner query, allowing Superset pagination on outer query
            sql = f"""
            WITH limited_visits AS (
                SELECT
                    uv.id,
                    uv.xform_id,
                    uv.user_id,
                    uv.opportunity_id,
                    uv.visit_date,
                    uv.entity_id,
                    uv.entity_name,
                    uv.location,
                    uv.status,
                    uv.reason,
                    uv.flag_reason,
                    uv.flagged,
                    uv.deliver_unit_id
                FROM opportunity_uservisit uv
                WHERE {" AND ".join(where_conditions)}
                ORDER BY uv.visit_date DESC
                LIMIT {count}
            )
            SELECT
                lv.*,
                u.username,
                u.name as user_name,
                COALESCE(unit_app.cc_domain, opp_app.cc_domain) as cc_domain,
                COALESCE(unit_app.cc_app_id, opp_app.cc_app_id) as cc_app_id
            FROM limited_visits lv
            LEFT JOIN users_user u ON u.id = lv.user_id
            LEFT JOIN opportunity_opportunity o ON o.id = lv.opportunity_id
            LEFT JOIN opportunity_deliverunit du ON du.id = lv.deliver_unit_id
            LEFT JOIN opportunity_commcareapp unit_app ON unit_app.id = du.app_id
            LEFT JOIN opportunity_commcareapp opp_app ON opp_app.id = o.deliver_app_id
            """

        elif audit_type == "last_n_across_all":
            # Get last N visits across ALL opportunities combined (limit applies globally)
            # Use CTE to apply LIMIT in inner query, allowing Superset pagination on outer query
            sql = f"""
            WITH limited_visits AS (
                SELECT
                    uv.id,
                    uv.xform_id,
                    uv.user_id,
                    uv.opportunity_id,
                    uv.visit_date,
                    uv.entity_id,
                    uv.entity_name,
                    uv.location,
                    uv.status,
                    uv.reason,
                    uv.flag_reason,
                    uv.flagged,
                    uv.deliver_unit_id
                FROM opportunity_uservisit uv
                WHERE {" AND ".join(where_conditions)}
                ORDER BY uv.visit_date DESC
                LIMIT {count}
            )
            SELECT
                lv.*,
                u.username,
                u.name as user_name,
                COALESCE(unit_app.cc_domain, opp_app.cc_domain) as cc_domain,
                COALESCE(unit_app.cc_app_id, opp_app.cc_app_id) as cc_app_id
            FROM limited_visits lv
            LEFT JOIN users_user u ON u.id = lv.user_id
            LEFT JOIN opportunity_opportunity o ON o.id = lv.opportunity_id
            LEFT JOIN opportunity_deliverunit du ON du.id = lv.deliver_unit_id
            LEFT JOIN opportunity_commcareapp unit_app ON unit_app.id = du.app_id
            LEFT JOIN opportunity_commcareapp opp_app ON opp_app.id = o.deliver_app_id
            """

        else:
            raise ValueError(f"Unknown audit_type: {audit_type}")

        df = self.superset_extractor.execute_query(sql)

        if df is None or df.empty:
            return []

        visits = []
        for _, row in df.iterrows():
            visit_dict = row.to_dict()
            # Convert NaT/NaN to None
            for key, value in visit_dict.items():
                if hasattr(value, "__class__") and "NaTType" in str(value.__class__):
                    visit_dict[key] = None
            visits.append(visit_dict)

        # FALLBACK: If cc_domain is NULL, try to get it from fallback CSV
        # This is needed because opportunity_commcareapp table is not synced to Superset
        missing_domain_count = sum(1 for v in visits if not v.get("cc_domain"))
        if missing_domain_count > 0:
            fallback_data = self._load_commcare_app_fallback()
            if fallback_data:
                # Get opportunity deliver_app_id mapping from Superset
                opp_app_sql = f"""
                SELECT id, deliver_app_id
                FROM opportunity_opportunity
                WHERE id IN ({ids_str})
                """
                opp_df = self.superset_extractor.execute_query(opp_app_sql)
                if opp_df is not None and not opp_df.empty:
                    opp_to_app = {
                        row["id"]: row["deliver_app_id"] for _, row in opp_df.iterrows() if row["deliver_app_id"]
                    }

                    # Enrich visits with fallback data
                    enriched_count = 0
                    for visit in visits:
                        if not visit.get("cc_domain"):
                            opp_id = visit.get("opportunity_id")
                            app_id = opp_to_app.get(opp_id)
                            if app_id and app_id in fallback_data:
                                visit["cc_domain"] = fallback_data[app_id]["cc_domain"]
                                visit["cc_app_id"] = fallback_data[app_id]["cc_app_id"]
                                enriched_count += 1

                    if enriched_count > 0:
                        print(f"[INFO] Using fallback CSV for domain info ({len(fallback_data)} apps configured)")

        return visits

    def get_user_visit_ids_for_audit(
        self,
        opportunity_ids: list[int],
        audit_type: str,
        start_date: date = None,
        end_date: date = None,
        count: int = None,
        user_id: int = None,
    ) -> list[int] | list[tuple[int, int]]:
        """
        Get UserVisit IDs based on audit criteria (lightweight query for sampling).

        This is a lightweight version of get_user_visits_for_audit that only returns IDs.
        Used for preview with sampling to avoid loading full visit data.

        Args:
            opportunity_ids: List of opportunity IDs
            audit_type: 'date_range', 'last_n_per_flw', 'last_n_per_opp', or 'last_n_across_all'
            start_date: Start date for date_range type
            end_date: End date for date_range type
            count: Number of visits for last_n types
            user_id: Optional filter for specific user (for per_flw granularity)

        Returns:
            - For 'last_n_across_all': List of tuples (visit_id, opportunity_id)
            - For other types: List of visit IDs
        """
        if not opportunity_ids:
            return []

        ids_str = ",".join(str(id) for id in opportunity_ids)

        # Base SELECT clause - only ID
        select_clause = """
        SELECT uv.id
        FROM opportunity_uservisit uv
        """

        # Build WHERE clause based on audit type
        where_conditions = [f"uv.opportunity_id IN ({ids_str})", "uv.status = 'approved'"]

        if user_id:
            where_conditions.append(f"uv.user_id = {user_id}")

        if audit_type == "date_range":
            if start_date and end_date:
                where_conditions.append(f"DATE(uv.visit_date) >= '{start_date}'")
                where_conditions.append(f"DATE(uv.visit_date) <= '{end_date}'")
            sql = f"""
            {select_clause}
            WHERE {" AND ".join(where_conditions)}
            ORDER BY uv.visit_date DESC
            """

        elif audit_type == "last_n_per_flw":
            # Get last N visits per FLW
            sql = f"""
            WITH ranked_visits AS (
                SELECT uv.id, uv.user_id, uv.visit_date,
                       ROW_NUMBER() OVER (PARTITION BY uv.user_id ORDER BY uv.visit_date DESC) as rank
                FROM opportunity_uservisit uv
                WHERE {" AND ".join(where_conditions)}
            )
            SELECT id
            FROM ranked_visits
            WHERE rank <= {count}
            ORDER BY user_id, visit_date DESC
            """

        elif audit_type == "last_n_per_opp":
            # Get last N visits per opportunity (limit applies per opportunity, not globally)
            # NOTE: This is handled at the caller level by calling this method once per opportunity
            # Use CTE to apply LIMIT in inner query, allowing Superset pagination on outer query
            sql = f"""
            WITH limited_visits AS (
                SELECT uv.id
                FROM opportunity_uservisit uv
                WHERE {" AND ".join(where_conditions)}
                ORDER BY uv.visit_date DESC
                LIMIT {count}
            )
            SELECT id FROM limited_visits
            """

        elif audit_type == "last_n_across_all":
            # Get last N visits across ALL opportunities combined (limit applies globally)
            # Use CTE to apply LIMIT in inner query, allowing Superset pagination on outer query
            # Also select opportunity_id so caller can track which opp each visit belongs to
            sql = f"""
            WITH limited_visits AS (
                SELECT uv.id, uv.opportunity_id
                FROM opportunity_uservisit uv
                WHERE {" AND ".join(where_conditions)}
                ORDER BY uv.visit_date DESC
                LIMIT {count}
            )
            SELECT id, opportunity_id FROM limited_visits
            """

        else:
            raise ValueError(f"Unknown audit_type: {audit_type}")

        df = self.superset_extractor.execute_query(sql)

        if df is None or df.empty:
            return []

        # For last_n_across_all, return list of tuples (id, opportunity_id)
        # For other types, just return IDs for backwards compatibility
        if audit_type == "last_n_across_all":
            return list(df[["id", "opportunity_id"]].itertuples(index=False, name=None))
        return df["id"].tolist()

    def get_user_visits_by_ids(self, visit_ids: list[int]) -> list[dict]:
        """
        Get full UserVisit records for specific visit IDs.

        This is used to load only sampled visits during audit creation.

        Args:
            visit_ids: List of visit IDs to load

        Returns:
            List of visit dictionaries
        """
        if not visit_ids:
            return []

        ids_str = ",".join(str(id) for id in visit_ids)

        # Same SELECT clause as get_user_visits_for_audit
        sql = """
        SELECT
            uv.id,
            uv.xform_id,
            uv.user_id,
            uv.opportunity_id,
            uv.visit_date,
            uv.entity_id,
            uv.entity_name,
            uv.location,
            uv.status,
            uv.reason,
            uv.flag_reason,
            uv.flagged,
            uv.deliver_unit_id,
            u.username,
            u.name as user_name,
            COALESCE(unit_app.cc_domain, opp_app.cc_domain) as cc_domain,
            COALESCE(unit_app.cc_app_id, opp_app.cc_app_id) as cc_app_id
        FROM opportunity_uservisit uv
        LEFT JOIN users_user u ON u.id = uv.user_id
        LEFT JOIN opportunity_opportunity o ON o.id = uv.opportunity_id
        LEFT JOIN opportunity_deliverunit du ON du.id = uv.deliver_unit_id
        LEFT JOIN opportunity_commcareapp unit_app ON unit_app.id = du.app_id
        LEFT JOIN opportunity_commcareapp opp_app ON opp_app.id = o.deliver_app_id
        WHERE uv.id IN ({ids_str})
        ORDER BY uv.visit_date DESC
        """

        df = self.superset_extractor.execute_query(sql.format(ids_str=ids_str))
        if df is None or df.empty:
            return []

        visits = []
        for _, row in df.iterrows():
            visit_dict = row.to_dict()
            # Convert NaT/NaN to None
            for key, value in visit_dict.items():
                if hasattr(value, "__class__") and "NaTType" in str(value.__class__):
                    visit_dict[key] = None
            visits.append(visit_dict)

        # FALLBACK: If cc_domain is NULL, try to get it from fallback CSV
        missing_domain_count = sum(1 for v in visits if not v.get("cc_domain"))
        if missing_domain_count > 0:
            fallback_data = self._load_commcare_app_fallback()
            if fallback_data:
                # Get opportunity deliver_app_id mapping from Superset
                opp_ids = list({v["opportunity_id"] for v in visits if v.get("opportunity_id")})
                if opp_ids:
                    opp_ids_str = ",".join(str(id) for id in opp_ids)
                    opp_app_sql = f"""
                    SELECT id, deliver_app_id
                    FROM opportunity_opportunity
                    WHERE id IN ({opp_ids_str})
                    """
                    opp_df = self.superset_extractor.execute_query(opp_app_sql)
                    if opp_df is not None and not opp_df.empty:
                        opp_to_app = {
                            row["id"]: row["deliver_app_id"] for _, row in opp_df.iterrows() if row["deliver_app_id"]
                        }

                        # Enrich visits with fallback data
                        enriched_count = 0
                        for visit in visits:
                            if not visit.get("cc_domain"):
                                opp_id = visit.get("opportunity_id")
                                app_id = opp_to_app.get(opp_id)
                                if app_id and app_id in fallback_data:
                                    visit["cc_domain"] = fallback_data[app_id]["cc_domain"]
                                    visit["cc_app_id"] = fallback_data[app_id]["cc_app_id"]
                                    enriched_count += 1

                        if enriched_count > 0:
                            print(f"[INFO] Using fallback CSV for domain info ({len(fallback_data)} apps configured)")

        return visits

    def close(self):
        """Clean up resources."""
        if self.http_client:
            self.http_client.close()
        if self.superset_extractor:
            self.superset_extractor.close()


# Convenience functions for common operations


def search_programs(query: str = "", limit: int = 50) -> list[Program]:
    """Quick search for programs."""
    facade = ConnectAPIFacade()
    if facade.authenticate():
        try:
            return facade.search_programs(query, limit)
        finally:
            facade.close()
    return []


def get_opportunity_info(opportunity_id: int) -> Opportunity | None:
    """Get detailed information about a specific opportunity."""
    facade = ConnectAPIFacade()
    if facade.authenticate():
        try:
            # First, find which program this opportunity belongs to
            sql = f"""
            SELECT mo.program_id
            FROM program_managedopportunity mo
            WHERE mo.opportunity_ptr_id = {opportunity_id}
            """
            df = facade.superset_extractor.execute_query(sql)
            if df is not None and not df.empty:
                program_id = df.iloc[0]["program_id"]
                opportunities = facade.get_opportunities_by_program(program_id)
                for opp in opportunities:
                    if opp.id == opportunity_id:
                        return opp
        finally:
            facade.close()
    return None

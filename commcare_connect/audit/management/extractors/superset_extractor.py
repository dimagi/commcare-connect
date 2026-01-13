#!/usr/bin/env python3
"""
Superset Data Extractor

Simple, clean way to extract data from Superset using SQL queries.
"""

import os
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

# SQL queries are now passed as parameters to execute_query method
# No need to import hardcoded queries

# Environment variables are loaded by Django's settings system


class SupersetExtractor:
    """Simple Superset data extraction class."""

    def __init__(self, superset_url: str | None = None, username: str | None = None, password: str | None = None):
        """
        Initialize the Superset extractor.

        Args:
            superset_url: Superset URL (defaults to SUPERSET_URL env var)
            username: Username (defaults to SUPERSET_USERNAME env var)
            password: Password (defaults to SUPERSET_PASSWORD env var)
        """
        # Configuration from parameters or environment
        self.superset_url = superset_url or os.getenv("SUPERSET_URL")
        self.username = username or os.getenv("SUPERSET_USERNAME")
        self.password = password or os.getenv("SUPERSET_PASSWORD")

        # Validate required fields
        if not all([self.superset_url, self.username, self.password]):
            missing = []
            if not self.superset_url:
                missing.append("SUPERSET_URL")
            if not self.username:
                missing.append("SUPERSET_USERNAME")
            if not self.password:
                missing.append("SUPERSET_PASSWORD")
            raise ValueError(f"Missing required configuration: {', '.join(missing)}")

        # Normalize URL
        self.superset_url = self.superset_url.rstrip("/")
        if not self.superset_url.startswith(("http://", "https://")):
            self.superset_url = f"https://{self.superset_url}"

        # Configuration defaults
        self.database_id = int(os.getenv("SUPERSET_DATABASE_ID", 4))
        self.schema = os.getenv("SUPERSET_SCHEMA", "public")
        self.chunk_size = int(os.getenv("SUPERSET_CHUNK_SIZE", 10000))
        self.timeout = int(os.getenv("SUPERSET_TIMEOUT", 120))

        # Session management
        self.session = None
        self.access_token = None
        self.csrf_token = None

    def authenticate(self) -> bool:
        """Authenticate with Superset and get access tokens."""
        self.session = requests.Session()

        # Authenticate
        auth_url = f"{self.superset_url}/api/v1/security/login"
        auth_payload = {"username": self.username, "password": self.password, "provider": "db", "refresh": True}

        auth_response = self.session.post(auth_url, json=auth_payload, timeout=self.timeout)
        if auth_response.status_code != 200:
            print(f"Authentication failed: {auth_response.text}")
            return False

        auth_data = auth_response.json()
        if "access_token" not in auth_data:
            print(f"No access token in response: {auth_data}")
            return False

        self.access_token = auth_data["access_token"]

        # Set up session headers with authorization
        self.session.headers.update(
            {"Authorization": f"Bearer {self.access_token}", "Content-Type": "application/json"}
        )

        # Get CSRF token
        csrf_url = f"{self.superset_url}/api/v1/security/csrf_token/"
        csrf_response = self.session.get(csrf_url, timeout=self.timeout)
        if csrf_response.status_code == 200:
            csrf_data = csrf_response.json()
            self.csrf_token = csrf_data.get("result")
            if self.csrf_token:
                self.session.headers.update({"x-csrftoken": self.csrf_token, "Referer": f"{self.superset_url}/sqllab"})

        print("[OK] Authentication successful")
        return True

    def execute_query(
        self, sql_query: str, verbose: bool = False, output_file: str = None, resume: bool = True
    ) -> pd.DataFrame | None:
        """Execute a SQL query with pagination and return results as DataFrame."""
        if not self.session or not self.access_token:
            if not self.authenticate():
                return None

        execute_url = f"{self.superset_url}/api/v1/sqllab/execute/"
        all_columns = None
        offset = 0
        total_rows = 0
        chunk_num = 1

        # Check if resuming from existing file
        if output_file and resume and os.path.exists(output_file):
            # Count existing rows to determine offset
            with open(output_file, encoding="utf-8") as f:
                total_lines = sum(1 for _ in f)
                existing_rows = total_lines - 1  # Subtract header row
            offset = existing_rows
            total_rows = existing_rows
            chunk_num = (existing_rows // self.chunk_size) + 1
            print(f"[FILE] Found {total_lines:,} lines in file ({existing_rows:,} data rows + 1 header)")
            print(f"[RESUME] Resuming from row {existing_rows:,} (chunk {chunk_num})")
            print(f"[LOCATION] Starting offset: {offset:,}, chunk size: {self.chunk_size:,}")

        # For memory efficiency with large datasets
        if output_file:
            temp_files = []

        while True:
            # Add OFFSET and LIMIT to the SQL (ensure proper formatting)
            clean_query = sql_query.strip().rstrip(";")
            paginated_sql = f"{clean_query}\nOFFSET {offset}\nLIMIT {self.chunk_size}"

            # Query with offset and limit for chunking

            payload = {
                "ctas_method": "TABLE",
                "database_id": self.database_id,
                "expand_data": False,
                "json": True,
                "queryLimit": self.chunk_size,
                "runAsync": False,
                "schema": self.schema,
                "select_as_cta": False,
                "sql": paginated_sql,
                "templateParams": "",
                "tmp_table_name": "",
            }

            response = self.session.post(execute_url, json=payload, timeout=self.timeout)
            result = response.json()

            if response.status_code != 200 or result.get("status") != "success":
                if verbose:
                    print(f"Query execution failed: {result}")
                break

            # Get data and columns
            chunk_data = result.get("data", [])
            columns = result.get("columns", [])

            # Store column metadata from first chunk (verbose output suppressed)

            if not chunk_data:
                break

            # Store columns from first chunk
            if all_columns is None:
                all_columns = columns

            chunk_rows = len(chunk_data)
            total_rows += chunk_rows

            # Write chunk to disk immediately for memory efficiency
            if output_file:
                chunk_df = pd.DataFrame(chunk_data, columns=[col["name"] for col in all_columns])
                if chunk_num == 1 and not (resume and os.path.exists(output_file)):
                    # First chunk - create file with headers (only if not resuming)
                    chunk_df.to_csv(output_file, index=False, mode="w")
                else:
                    # Subsequent chunks or resuming - append without headers
                    chunk_df.to_csv(output_file, index=False, mode="a", header=False)
                print(f"[OK] Written to {output_file}")
            else:
                # Legacy behavior - keep in memory
                if "all_data" not in locals():
                    all_data = []
                all_data.extend(chunk_data)

            # If we got fewer rows than chunk_size, we've reached the end
            if chunk_rows < self.chunk_size:
                break

            # Prepare for next chunk
            offset += self.chunk_size
            chunk_num += 1

            # Small delay to be nice to the server
            time.sleep(0.5)

        # Query complete - {total_rows} rows retrieved

        # If writing to file, return the file path info
        if output_file:
            if total_rows == 0:
                # Silently return None for empty results
                return None
            print(f"[ICON] Data written to: {output_file}")
            # Return a simple DataFrame with summary info
            return pd.DataFrame({"total_rows": [total_rows], "output_file": [output_file]})

        # Legacy behavior - create DataFrame from memory
        if "all_data" not in locals() or not all_data or not all_columns:
            # Silently return None for empty results
            return None

        column_names = [col.get("name", f"col_{i}") for i, col in enumerate(all_columns)]
        df = pd.DataFrame(all_data, columns=column_names)

        return df

    def export_query_to_csv(
        self, sql_query: str, output_filename: str | None = None, verbose: bool = False
    ) -> str | None:
        """Execute a SQL query and export results to CSV."""
        df = self.execute_query(sql_query, verbose=verbose)
        if df is None:
            return None

        # Ensure data directory exists
        data_dir = Path("data")
        data_dir.mkdir(exist_ok=True)

        # Generate output filename
        if output_filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_filename = f"superset_export_{timestamp}"

        output_path = data_dir / f"{output_filename}.csv"

        # Export to CSV
        df.to_csv(output_path, index=False, encoding="utf-8")

        print(f"[OK] Data exported to: {output_path}")
        print(f"   Shape: {df.shape[0]:,} rows × {df.shape[1]} columns")

        return str(output_path)

    def get_sql_query_specific_opportunities(self) -> str:
        """Get the SQL query for specific opportunities."""
        # This method is deprecated - pass queries directly to execute_query
        raise NotImplementedError("Pass SQL queries directly to execute_query method")

    def get_sql_query_all_opportunities(self) -> str:
        """Get the SQL query for all opportunities."""
        # This method is deprecated - pass queries directly to execute_query
        raise NotImplementedError("Pass SQL queries directly to execute_query method")

    def close(self):
        """Close the session and clean up resources."""
        if self.session:
            self.session.close()
            self.session = None


def main():
    """Main function to demonstrate the Superset extractor usage."""
    # Initialize extractor (reads from .env automatically)
    extractor = SupersetExtractor()

    # Authenticate
    if not extractor.authenticate():
        print("[ERROR] Authentication failed")
        return

    # Execute the specific opportunities query
    print("\n[RUN] Executing specific_opportunities query...")
    query = extractor.get_sql_query_specific_opportunities()
    print(f"Query preview: {query[:100]}...")

    # Execute and get results
    df = extractor.execute_query(query, verbose=True)

    if df is not None:
        print("\n[OK] Query executed successfully!")
        print(f"[DATA] Results: {len(df)} rows, {len(df.columns)} columns")
        print(f"[DATA] Columns: {list(df.columns)}")
        print("\n[DATA] Sample data:")
        print(df.head())

        # Export to CSV
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_filename = f"opportunity_visits_specific_{timestamp}"

        print("\n[EXPORT] Exporting to CSV...")
        csv_path = extractor.export_query_to_csv(query, output_filename, verbose=True)

        if csv_path:
            print(f"[OK] Export successful: {csv_path}")
        else:
            print("[ERROR] Export failed")
    else:
        print("[ERROR] Query execution failed")

    extractor.close()
    print("\n[DONE] Extractor closed")


if __name__ == "__main__":
    main()

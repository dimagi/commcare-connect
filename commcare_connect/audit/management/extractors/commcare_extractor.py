#!/usr/bin/env python3
"""
CommCare Data Extractor

Simple, clean way to extract data from CommCare HQ using their API.
Supports forms, cases, and attachment downloads.
"""

import os
import time
from pathlib import Path
from typing import Any

import requests

# Environment variables are loaded by Django's settings system


class CommCareExtractor:
    """Simple CommCare data extraction class."""

    def __init__(self, domain: str, username: str | None = None, api_key: str | None = None):
        """
        Initialize the CommCare extractor.

        Args:
            domain: CommCare domain/project space name (required)
            username: API username (defaults to COMMCARE_USERNAME env var)
            api_key: API key (defaults to COMMCARE_API_KEY env var)
        """
        # Domain is required parameter
        if not domain:
            raise ValueError("Domain is required")
        self.domain = domain

        # Credentials from parameters or environment
        self.username = username or os.getenv("COMMCARE_USERNAME")
        self.api_key = api_key or os.getenv("COMMCARE_API_KEY")

        # Validate credentials
        if not self.username or not self.api_key:
            missing = []
            if not self.username:
                missing.append("COMMCARE_USERNAME")
            if not self.api_key:
                missing.append("COMMCARE_API_KEY")
            raise ValueError(f"Missing required credentials: {', '.join(missing)}")

        # API configuration
        self.base_url = f"https://www.commcarehq.org/a/{self.domain}/api/v0.5"
        self.timeout = int(os.getenv("COMMCARE_TIMEOUT", 30))
        self.default_limit = int(os.getenv("COMMCARE_PAGE_LIMIT", 1000))

        # Session management
        self.session = requests.Session()
        self.session.auth = (self.username, self.api_key)

    def get_forms(
        self,
        app_id: str | None = None,
        limit: int | None = None,
        received_start: str | None = None,
        received_end: str | None = None,
        include_archived: bool = False,
        verbose: bool = False,
    ) -> list[dict[str, Any]]:
        """
        Fetch forms from CommCare with pagination.

        Args:
            app_id: Filter by application ID (optional)
            limit: Maximum number of forms to retrieve (None = all forms)
            received_start: Start date filter (ISO format: YYYY-MM-DD)
            received_end: End date filter (ISO format: YYYY-MM-DD)
            include_archived: Include archived forms
            verbose: Print progress information

        Returns:
            List of form dictionaries
        """
        url = f"{self.base_url}/form/"
        all_forms = []
        offset = 0
        page_limit = self.default_limit

        if verbose:
            print(f"Fetching forms from domain: {self.domain}")
            if app_id:
                print(f"   App ID filter: {app_id}")

        while True:
            # Build parameters
            params = {"limit": page_limit, "offset": offset}

            if app_id:
                params["app_id"] = app_id
            if received_start:
                params["received_on_start"] = received_start
            if received_end:
                params["received_on_end"] = received_end
            if include_archived:
                params["include_archived"] = "true"

            try:
                response = self.session.get(url, params=params, timeout=self.timeout)
                response.raise_for_status()

                data = response.json()
                forms = data.get("objects", [])

                if not forms:
                    break

                all_forms.extend(forms)

                if verbose:
                    print(f"Retrieved {len(forms)} forms (total: {len(all_forms)})")

                # Check if we've reached the requested limit
                if limit and len(all_forms) >= limit:
                    all_forms = all_forms[:limit]
                    break

                # Check if there are more pages
                if len(forms) < page_limit:
                    break

                offset += page_limit
                time.sleep(0.2)  # Be nice to the API

            except requests.exceptions.RequestException as e:
                print(f"Error fetching forms: {e}")
                break

        if verbose:
            print(f"Total forms retrieved: {len(all_forms)}")

        return all_forms

    def get_cases(
        self,
        case_type: str | None = None,
        limit: int | None = None,
        server_modified_start: str | None = None,
        server_modified_end: str | None = None,
        verbose: bool = False,
    ) -> list[dict[str, Any]]:
        """
        Fetch cases from CommCare with pagination.

        Args:
            case_type: Filter by case type (optional)
            limit: Maximum number of cases to retrieve (None = all cases)
            server_modified_start: Start date filter (ISO format: YYYY-MM-DD)
            server_modified_end: End date filter (ISO format: YYYY-MM-DD)
            verbose: Print progress information

        Returns:
            List of case dictionaries
        """
        url = f"{self.base_url}/case/"
        all_cases = []
        offset = 0
        page_limit = self.default_limit

        if verbose:
            print(f"Fetching cases from domain: {self.domain}")
            if case_type:
                print(f"   Case type filter: {case_type}")

        while True:
            # Build parameters
            params = {"limit": page_limit, "offset": offset}

            if case_type:
                params["type"] = case_type
            if server_modified_start:
                params["server_date_modified_start"] = server_modified_start
            if server_modified_end:
                params["server_date_modified_end"] = server_modified_end

            try:
                response = self.session.get(url, params=params, timeout=self.timeout)
                response.raise_for_status()

                data = response.json()
                cases = data.get("objects", [])

                if not cases:
                    break

                all_cases.extend(cases)

                if verbose:
                    print(f"Retrieved {len(cases)} cases (total: {len(all_cases)})")

                # Check if we've reached the requested limit
                if limit and len(all_cases) >= limit:
                    all_cases = all_cases[:limit]
                    break

                # Check if there are more pages
                if len(cases) < page_limit:
                    break

                offset += page_limit
                time.sleep(0.2)  # Be nice to the API

            except requests.exceptions.RequestException as e:
                print(f"Error fetching cases: {e}")
                break

        if verbose:
            print(f"Total cases retrieved: {len(all_cases)}")

        return all_cases

    def download_attachment(self, attachment_url: str, output_path: Path, verbose: bool = False) -> bool:
        """
        Download a file attachment from CommCare.

        Args:
            attachment_url: Full URL to the attachment
            output_path: Path where file should be saved
            verbose: Print progress information

        Returns:
            True if successful, False otherwise
        """
        try:
            response = self.session.get(attachment_url, timeout=self.timeout, stream=True)
            response.raise_for_status()

            # Create parent directory if needed
            output_path.parent.mkdir(parents=True, exist_ok=True)

            # Write file
            with open(output_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)

            if verbose:
                file_size = output_path.stat().st_size
                print(f"Downloaded: {output_path.name} ({file_size:,} bytes)")

            return True

        except requests.exceptions.RequestException as e:
            print(f"Error downloading attachment: {e}")
            return False

    def download_form_attachments(
        self, form: dict[str, Any], output_dir: Path, file_types: list[str] | None = None, verbose: bool = False
    ) -> list[Path]:
        """
        Download all attachments from a form.

        Args:
            form: Form dictionary from get_forms()
            output_dir: Directory where files should be saved
            file_types: List of file extensions to download (e.g., ['.jpg', '.png'])
                       If None, downloads all attachments
            verbose: Print progress information

        Returns:
            List of downloaded file paths
        """
        downloaded_files = []
        attachments = form.get("attachments", {})

        if not attachments:
            return downloaded_files

        for filename, attachment_info in attachments.items():
            # Filter by file type if specified
            if file_types:
                if not any(filename.lower().endswith(ext.lower()) for ext in file_types):
                    continue

            # Extract URL from attachment info
            # CommCare returns attachments as dict with 'url' key
            if isinstance(attachment_info, dict):
                attachment_url = attachment_info.get("url")
            else:
                attachment_url = attachment_info

            if not attachment_url:
                if verbose:
                    print(f"[WARNING] No URL found for attachment: {filename}")
                continue

            # Create output path
            output_path = output_dir / filename

            # Download file
            if self.download_attachment(attachment_url, output_path, verbose=verbose):
                downloaded_files.append(output_path)

        return downloaded_files

    def close(self):
        """Close the session and clean up resources."""
        if self.session:
            self.session.close()
            self.session = None


def main():
    """Example usage of CommCareExtractor."""
    try:
        # Initialize extractor with domain (credentials read from .env)
        domain = "your_domain_name"  # Replace with your domain
        extractor = CommCareExtractor(domain=domain)

        print(f"CommCare Extractor initialized for domain: {extractor.domain}")

        # Example 1: Fetch forms
        print("\n" + "=" * 60)
        print("Example 1: Fetching forms")
        print("=" * 60)

        # You can optionally specify an app_id
        app_id = "your_app_id"  # Replace with your app ID

        # Fetch forms
        forms = extractor.get_forms(app_id=app_id, limit=10, verbose=True)
        if forms:
            print(f"\nSample form keys: {list(forms[0].keys())}")
            print(f"Total forms fetched: {len(forms)}")
        else:
            print("No forms found")

        # Example 2: Fetch cases
        print("\n" + "=" * 60)
        print("Example 2: Fetching cases")
        print("=" * 60)

        cases = extractor.get_cases(limit=5, verbose=True)
        if cases:
            print(f"\nSample case keys: {list(cases[0].keys())}")

        # Example 3: Download attachments from forms
        print("\n" + "=" * 60)
        print("Example 3: Downloading attachments")
        print("=" * 60)

        if app_id:
            forms_with_attachments = extractor.get_forms(app_id=app_id, limit=5, verbose=True)

            # Create output directory
            output_dir = Path("data/attachments")

            for form in forms_with_attachments:
                if form.get("attachments"):
                    print(f"\nDownloading attachments from form: {form.get('id')}")
                    downloaded = extractor.download_form_attachments(
                        form, output_dir, file_types=[".jpg", ".jpeg", ".png"], verbose=True  # Only download images
                    )
                    print(f"Downloaded {len(downloaded)} files")

        extractor.close()
        print("\nExamples completed successfully")

    except ValueError as e:
        print(f"Configuration error: {e}")
        print("\nPlease set the following environment variables in your .env file:")
        print("  - COMMCARE_USERNAME (your API username)")
        print("  - COMMCARE_API_KEY (your API key)")
        print("\nAnd provide domain as a parameter when initializing the extractor.")
        print("Example: CommCareExtractor(domain='your_domain_name')")
    except Exception as e:
        print(f"Unexpected error: {e}")


if __name__ == "__main__":
    main()

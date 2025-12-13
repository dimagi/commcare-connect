"""
Tests for AuditDefinition model export/import functionality.

These tests verify that AuditDefinition can be correctly exported to JSON
and imported back, preserving all configuration and preview data.
"""

from datetime import date

import pytest
from django.contrib.auth import get_user_model
from django.test import TestCase

from commcare_connect.audit.models import AuditDefinition, AuditSession

User = get_user_model()


@pytest.mark.django_db
class AuditDefinitionTest(TestCase):
    """Test AuditDefinition model functionality"""

    def test_audit_definition_creation(self):
        """Test creating an AuditDefinition with all fields"""
        user = User.objects.create_user(username="testuser", email="test@example.com")

        audit_def = AuditDefinition.objects.create(
            name="Test Audit",
            created_by=user,
            opportunity_ids=[1, 2, 3],
            audit_type="last_n_across_all",
            granularity="combined",
            count_across_all=10000,
            sample_percentage=3,
            preview_data=[
                {
                    "opportunity_id": 0,
                    "opportunity_name": "3 opportunities",
                    "total_flws": 59,
                    "total_visits": 300,
                    "total_visits_before_sampling": 10000,
                    "sample_percentage": 3,
                    "avg_visits_per_flw": 5.1,
                    "sessions_to_create": 1,
                }
            ],
            status=AuditDefinition.Status.READY,
        )

        assert audit_def.id is not None
        assert audit_def.name == "Test Audit"
        assert audit_def.created_by == user
        assert len(audit_def.opportunity_ids) == 3
        assert audit_def.audit_type == "last_n_across_all"
        assert audit_def.status == AuditDefinition.Status.READY

    def test_to_dict_export(self):
        """Test exporting AuditDefinition to dictionary"""
        user = User.objects.create_user(username="testuser", email="test@example.com")

        audit_def = AuditDefinition.objects.create(
            name="Export Test",
            created_by=user,
            opportunity_ids=[411, 412, 516],
            audit_type="last_n_across_all",
            granularity="combined",
            count_across_all=10000,
            sample_percentage=3,
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
            preview_data=[
                {
                    "opportunity_id": 0,
                    "opportunity_name": "3 opportunities",
                    "total_flws": 59,
                    "total_visits": 300,
                    "sessions_to_create": 1,
                }
            ],
            status=AuditDefinition.Status.READY,
        )

        exported = audit_def.to_dict()

        assert exported["id"] == audit_def.id
        assert exported["name"] == "Export Test"
        assert exported["created_by"] == "testuser"
        assert exported["opportunity_ids"] == [411, 412, 516]
        assert exported["audit_type"] == "last_n_across_all"
        assert exported["granularity"] == "combined"
        assert exported["count_across_all"] == 10000
        assert exported["sample_percentage"] == 3
        assert exported["start_date"] == "2024-01-01"
        assert exported["end_date"] == "2024-12-31"
        assert exported["status"] == "ready"
        assert len(exported["preview_data"]) == 1
        assert exported["preview_data"][0]["total_flws"] == 59

    def test_from_dict_import(self):
        """Test importing AuditDefinition from dictionary"""
        user = User.objects.create_user(username="importer", email="import@example.com")

        exported_data = {
            "name": "Imported Audit",
            "opportunity_ids": [1, 2, 3],
            "audit_type": "last_n_per_flw",
            "granularity": "per_flw",
            "count_per_flw": 5,
            "sample_percentage": 50,
            "start_date": "2024-06-01",
            "end_date": "2024-06-30",
        }

        imported_def = AuditDefinition.from_dict(exported_data, user=user)
        imported_def.save()

        assert imported_def.id is not None
        assert imported_def.name == "Imported Audit"
        assert imported_def.created_by == user
        assert imported_def.opportunity_ids == [1, 2, 3]
        assert imported_def.audit_type == "last_n_per_flw"
        assert imported_def.granularity == "per_flw"
        assert imported_def.count_per_flw == 5
        assert imported_def.sample_percentage == 50
        assert imported_def.start_date == date(2024, 6, 1)
        assert imported_def.end_date == date(2024, 6, 30)
        assert imported_def.status == AuditDefinition.Status.DRAFT  # Imported as draft

    def test_export_import_roundtrip(self):
        """Test that export -> import preserves all data"""
        user = User.objects.create_user(username="roundtrip", email="roundtrip@example.com")

        # Create original
        original = AuditDefinition.objects.create(
            name="Roundtrip Test",
            created_by=user,
            opportunity_ids=[100, 200, 300],
            audit_type="date_range",
            granularity="per_opp",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 3, 31),
            sample_percentage=25,
            preview_data=[
                {
                    "opportunity_id": 100,
                    "opportunity_name": "Opp 100",
                    "total_flws": 20,
                    "total_visits": 500,
                    "sessions_to_create": 1,
                }
            ],
        )

        # Export
        exported = original.to_dict()

        # Import (simulating a new user importing the definition)
        new_user = User.objects.create_user(username="newuser", email="new@example.com")
        imported = AuditDefinition.from_dict(exported, user=new_user)
        imported.save()

        # Verify core fields are preserved
        assert imported.opportunity_ids == original.opportunity_ids
        assert imported.audit_type == original.audit_type
        assert imported.granularity == original.granularity
        assert imported.start_date == original.start_date
        assert imported.end_date == original.end_date
        assert imported.sample_percentage == original.sample_percentage
        # Note: preview_data is not imported (it needs to be regenerated)

    def test_to_criteria_dict(self):
        """Test converting AuditDefinition to criteria format for services"""
        audit_def = AuditDefinition.objects.create(
            opportunity_ids=[1, 2, 3],
            audit_type="last_n_across_all",
            granularity="combined",
            count_across_all=10000,
            sample_percentage=3,
            sample_cache_key="test_cache_key_123",
        )

        criteria = audit_def.to_criteria_dict()

        assert criteria["type"] == "last_n_across_all"
        assert criteria["granularity"] == "combined"
        assert criteria["samplePercentage"] == 3
        assert criteria["countAcrossAll"] == 10000
        assert criteria["sampleCacheKey"] == "test_cache_key_123"

    def test_to_criteria_dict_date_range(self):
        """Test criteria conversion for date_range audit type"""
        audit_def = AuditDefinition.objects.create(
            opportunity_ids=[1],
            audit_type="date_range",
            granularity="combined",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
            sample_percentage=100,
        )

        criteria = audit_def.to_criteria_dict()

        assert criteria["type"] == "date_range"
        assert criteria["startDate"] == "2024-01-01"
        assert criteria["endDate"] == "2024-12-31"
        assert criteria["samplePercentage"] == 100
        assert "countAcrossAll" not in criteria

    def test_get_summary(self):
        """Test getting a human-readable summary of the AuditDefinition"""
        audit_def = AuditDefinition.objects.create(
            opportunity_ids=[1, 2, 3],
            audit_type="last_n_across_all",
            granularity="combined",
            count_across_all=10000,
            sample_percentage=3,
            preview_data=[
                {
                    "total_flws": 59,
                    "total_visits": 300,
                    "sessions_to_create": 1,
                }
            ],
        )

        summary = audit_def.get_summary()

        assert summary["opportunities"] == 3
        assert summary["audit_type"] == "last_n_across_all"
        assert summary["granularity"] == "combined"
        assert summary["total_visits"] == 10000
        assert summary["sampling"] == "3%"
        assert summary["preview"]["flws"] == 59
        assert summary["preview"]["visits"] == 300
        assert summary["preview"]["sessions"] == 1

    def test_link_to_audit_session(self):
        """Test that AuditSessions can be linked to an AuditDefinition"""
        user = User.objects.create_user(username="auditor", email="auditor@example.com")

        # Create AuditDefinition
        audit_def = AuditDefinition.objects.create(
            created_by=user,
            opportunity_ids=[1, 2],
            audit_type="last_n_per_flw",
            granularity="combined",
            count_per_flw=5,
            status=AuditDefinition.Status.READY,
        )

        # Create AuditSession linked to definition
        session = AuditSession.objects.create(
            auditor_username="auditor",
            flw_username="test_flw",
            opportunity_name="Test Opportunity",
            domain="test-domain",
            app_id="test-app",
            start_date=date.today(),
            end_date=date.today(),
            audit_definition=audit_def,
        )

        # Verify link
        assert session.audit_definition == audit_def
        assert audit_def.audit_sessions.count() == 1
        assert audit_def.audit_sessions.first() == session

    def test_multiple_sessions_per_definition(self):
        """Test that multiple AuditSessions can share the same AuditDefinition"""
        user = User.objects.create_user(username="auditor", email="auditor@example.com")

        audit_def = AuditDefinition.objects.create(
            created_by=user,
            opportunity_ids=[1, 2, 3],
            audit_type="last_n_per_flw",
            granularity="per_flw",
            count_per_flw=5,
            status=AuditDefinition.Status.READY,
        )

        # Create multiple sessions
        sessions = []
        for i in range(3):
            session = AuditSession.objects.create(
                auditor_username="auditor",
                flw_username=f"flw_{i}",
                opportunity_name=f"Opportunity {i}",
                domain="test-domain",
                app_id="test-app",
                start_date=date.today(),
                end_date=date.today(),
                audit_definition=audit_def,
            )
            sessions.append(session)

        # Verify all sessions are linked
        assert audit_def.audits.count() == 3
        for session in sessions:
            assert session.template == audit_def

    def test_str_representation(self):
        """Test string representation of AuditDefinition"""
        audit_def = AuditDefinition.objects.create(
            name="My Audit",
            opportunity_ids=[1, 2, 3],
            audit_type="last_n_per_flw",
            granularity="per_flw",
        )

        assert str(audit_def) == "My Audit (last_n_per_flw, 3 opps)"

        # Test with no name
        audit_def_no_name = AuditDefinition.objects.create(
            opportunity_ids=[1, 2],
            audit_type="date_range",
            granularity="combined",
        )

        assert str(audit_def_no_name) == f"Audit {audit_def_no_name.id} (date_range, 2 opps)"

    def test_preview_cleans_up_all_definitions(self):
        """Test that creating a new preview cleans up ALL previous templates from the same user"""
        from unittest.mock import MagicMock

        from commcare_connect.audit.services.audit_creator import preview_audit_sessions

        user = User.objects.create_user(username="testuser", email="test@example.com")

        # Create some existing audit templates for this user
        old_def1 = AuditDefinition.objects.create(
            name="Old Template 1",
            created_by=user,
            opportunity_ids=[1, 2],
            audit_type="last_n_per_flw",
            granularity="per_flw",
            count_per_flw=5,
        )

        old_def2 = AuditDefinition.objects.create(
            name="Old Template 2",
            created_by=user,
            opportunity_ids=[3, 4],
            audit_type="last_n_per_flw",
            granularity="per_flw",
            count_per_flw=10,
        )

        old_def3 = AuditDefinition.objects.create(
            name="Old Template 3",
            created_by=user,
            opportunity_ids=[5, 6],
            audit_type="last_n_per_flw",
            granularity="per_flw",
            count_per_flw=15,
        )

        # Also create templates for a different user that should NOT be deleted
        other_user = User.objects.create_user(username="otheruser", email="other@example.com")
        other_def = AuditDefinition.objects.create(
            name="Other User's Template",
            created_by=other_user,
            opportunity_ids=[7, 8],
            audit_type="last_n_per_flw",
            granularity="per_flw",
            count_per_flw=20,
        )

        # Verify initial state
        assert AuditDefinition.objects.count() == 4

        # Mock facade for preview
        mock_facade = MagicMock()
        mock_opp = MagicMock()
        mock_opp.id = 1
        mock_opp.name = "Test Opportunity"
        mock_facade.search_opportunities.return_value = [mock_opp]
        mock_facade.get_flw_visit_counts_last_n_per_flw.return_value = {
            "total_flws": 10,
            "total_visits": 50,
            "date_range": ["2024-01-01", "2024-12-31"],
            "flws": [],
        }

        # Create a new preview - this should clean up ALL old templates from this user
        criteria = {"type": "last_n_per_flw", "granularity": "per_flw", "countPerFlw": 25}
        result = preview_audit_sessions(facade=mock_facade, opportunity_ids=[1], criteria=criteria, user=user)

        assert result.success is True

        # Verify ALL old templates from this user were deleted
        assert not AuditDefinition.objects.filter(id=old_def1.id).exists()
        assert not AuditDefinition.objects.filter(id=old_def2.id).exists()
        assert not AuditDefinition.objects.filter(id=old_def3.id).exists()

        # Verify other user's template was preserved
        assert AuditDefinition.objects.filter(id=other_def.id).exists()

        # Verify new template was created (only template for this user now)
        assert AuditDefinition.objects.count() == 2  # other_def, new_def
        new_def = result.template
        assert new_def.created_by == user
        assert new_def.count_per_flw == 25

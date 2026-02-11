"""Tests for multidb constants."""
import pytest

from commcare_connect.multidb.constants import (
    REPLICATION_MANAGER_MODELS,
    REPLICATION_PROGRAM_MANAGER_MODELS,
)


class TestMultidbConstants:
    """Tests for multidb replication constants."""

    def test_replication_manager_models_is_tuple(self):
        """Test that REPLICATION_MANAGER_MODELS is a tuple."""
        assert isinstance(REPLICATION_MANAGER_MODELS, tuple)

    def test_replication_program_manager_models_is_tuple(self):
        """Test that REPLICATION_PROGRAM_MANAGER_MODELS is a tuple."""
        assert isinstance(REPLICATION_PROGRAM_MANAGER_MODELS, tuple)

    def test_replication_manager_models_not_empty(self):
        """Test that REPLICATION_MANAGER_MODELS is not empty."""
        assert len(REPLICATION_MANAGER_MODELS) > 0

    def test_replication_program_manager_models_not_empty(self):
        """Test that REPLICATION_PROGRAM_MANAGER_MODELS is not empty."""
        assert len(REPLICATION_PROGRAM_MANAGER_MODELS) > 0

    def test_replication_manager_models_contains_strings(self):
        """Test that REPLICATION_MANAGER_MODELS contains only strings."""
        assert all(isinstance(model, str) for model in REPLICATION_MANAGER_MODELS)

    def test_replication_program_manager_models_contains_strings(self):
        """Test that REPLICATION_PROGRAM_MANAGER_MODELS contains only strings."""
        assert all(isinstance(model, str) for model in REPLICATION_PROGRAM_MANAGER_MODELS)

    def test_replication_manager_models_format(self):
        """Test that model names follow expected format (app.Model)."""
        for model in REPLICATION_MANAGER_MODELS:
            assert "." in model, f"Model {model} should be in 'app.Model' format"
            app, model_name = model.rsplit(".", 1)
            assert app, f"App name should not be empty in {model}"
            assert model_name, f"Model name should not be empty in {model}"

    def test_replication_program_manager_models_format(self):
        """Test that program manager model names follow expected format."""
        for model in REPLICATION_PROGRAM_MANAGER_MODELS:
            assert "." in model, f"Model {model} should be in 'app.Model' format"
            app, model_name = model.rsplit(".", 1)
            assert app, f"App name should not be empty in {model}"
            assert model_name, f"Model name should not be empty in {model}"

    def test_no_duplicate_models_in_manager_list(self):
        """Test that REPLICATION_MANAGER_MODELS has no duplicates."""
        assert len(REPLICATION_MANAGER_MODELS) == len(set(REPLICATION_MANAGER_MODELS))

    def test_no_duplicate_models_in_program_manager_list(self):
        """Test that REPLICATION_PROGRAM_MANAGER_MODELS has no duplicates."""
        assert len(REPLICATION_PROGRAM_MANAGER_MODELS) == len(
            set(REPLICATION_PROGRAM_MANAGER_MODELS)
        )

    def test_constants_are_immutable(self):
        """Test that constant tuples cannot be modified."""
        with pytest.raises(TypeError):
            REPLICATION_MANAGER_MODELS[0] = "new.Model"

        with pytest.raises(TypeError):
            REPLICATION_PROGRAM_MANAGER_MODELS[0] = "new.Model"

    def test_expected_models_present_in_manager_list(self):
        """Test that expected models are present in REPLICATION_MANAGER_MODELS."""
        # Based on the constants, verify key models are present
        expected_models = {
            "opportunity.Opportunity",
            "opportunity.OpportunityAccess",
            "users.User",
        }
        for model in expected_models:
            assert (
                model in REPLICATION_MANAGER_MODELS
            ), f"Expected model {model} not found in REPLICATION_MANAGER_MODELS"

    def test_expected_models_present_in_program_manager_list(self):
        """Test that expected models are present in REPLICATION_PROGRAM_MANAGER_MODELS."""
        # Based on the constants, verify key models are present
        expected_models = {
            "opportunity.Opportunity",
            "users.User",
        }
        for model in expected_models:
            assert (
                model in REPLICATION_PROGRAM_MANAGER_MODELS
            ), f"Expected model {model} not found in REPLICATION_PROGRAM_MANAGER_MODELS"

    def test_program_manager_subset_or_different_from_manager(self):
        """Test the relationship between manager and program manager model lists."""
        manager_set = set(REPLICATION_MANAGER_MODELS)
        program_manager_set = set(REPLICATION_PROGRAM_MANAGER_MODELS)

        # They can be subsets, supersets, or have different models
        # Just verify they're both valid sets with content
        assert len(manager_set) > 0
        assert len(program_manager_set) > 0
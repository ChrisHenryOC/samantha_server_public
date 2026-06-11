"""Tests for dispatch_constants — STATE_TO_STEP and SEVERITY_ORDER."""

from typing import get_args

import pytest

from samantha_server.engine.dispatch_constants import SEVERITY_ORDER, STATE_TO_STEP
from samantha_server.rules.spec import Severity


class TestStateToStep:
    def test_accessioning_maps_to_accessioning(self) -> None:
        assert STATE_TO_STEP["ACCESSIONING"] == "ACCESSIONING"

    def test_accepted_maps_to_sample_prep(self) -> None:
        assert STATE_TO_STEP["ACCEPTED"] == "SAMPLE_PREP"

    def test_missing_info_proceed_maps_to_sample_prep(self) -> None:
        assert STATE_TO_STEP["MISSING_INFO_PROCEED"] == "SAMPLE_PREP"

    def test_sample_prep_processing_maps_to_sample_prep(self) -> None:
        assert STATE_TO_STEP["SAMPLE_PREP_PROCESSING"] == "SAMPLE_PREP"

    def test_sample_prep_embedding_maps_to_sample_prep(self) -> None:
        assert STATE_TO_STEP["SAMPLE_PREP_EMBEDDING"] == "SAMPLE_PREP"

    def test_sample_prep_sectioning_maps_to_sample_prep(self) -> None:
        assert STATE_TO_STEP["SAMPLE_PREP_SECTIONING"] == "SAMPLE_PREP"

    def test_sample_prep_qc_maps_to_sample_prep(self) -> None:
        assert STATE_TO_STEP["SAMPLE_PREP_QC"] == "SAMPLE_PREP"

    def test_he_qc_maps_to_he_qc(self) -> None:
        assert STATE_TO_STEP["HE_QC"] == "HE_QC"

    def test_pathologist_he_review_maps_to_pathologist_he_review(self) -> None:
        assert STATE_TO_STEP["PATHOLOGIST_HE_REVIEW"] == "PATHOLOGIST_HE_REVIEW"

    def test_resulting_maps_to_resulting(self) -> None:
        assert STATE_TO_STEP["RESULTING"] == "RESULTING"

    def test_resulting_hold_maps_to_resulting(self) -> None:
        assert STATE_TO_STEP["RESULTING_HOLD"] == "RESULTING"

    def test_pathologist_signout_maps_to_resulting(self) -> None:
        assert STATE_TO_STEP["PATHOLOGIST_SIGNOUT"] == "RESULTING"

    def test_report_generation_maps_to_resulting(self) -> None:
        assert STATE_TO_STEP["REPORT_GENERATION"] == "RESULTING"

    def test_is_immutable(self) -> None:
        with pytest.raises((TypeError, AttributeError)):
            STATE_TO_STEP["NEW_KEY"] = "SOMETHING"  # type: ignore[index]


class TestSeverityOrder:
    def test_reject_has_lowest_index(self) -> None:
        assert SEVERITY_ORDER["REJECT"] == 0

    def test_hold_is_second(self) -> None:
        assert SEVERITY_ORDER["HOLD"] == 1

    def test_review_hold_is_third(self) -> None:
        """REVIEW_HOLD sits between HOLD and PROCEED."""
        assert SEVERITY_ORDER["REVIEW_HOLD"] == 2

    def test_proceed_is_fourth(self) -> None:
        """PROCEED shifts to index 3 after REVIEW_HOLD insertion."""
        assert SEVERITY_ORDER["PROCEED"] == 3

    def test_accept_has_highest_index(self) -> None:
        assert SEVERITY_ORDER["ACCEPT"] == 4

    def test_reject_beats_hold(self) -> None:
        assert SEVERITY_ORDER["REJECT"] < SEVERITY_ORDER["HOLD"]

    def test_hold_beats_review_hold(self) -> None:
        """HOLD (hard stop) beats REVIEW_HOLD (hold for review)."""
        assert SEVERITY_ORDER["HOLD"] < SEVERITY_ORDER["REVIEW_HOLD"]

    def test_review_hold_beats_proceed(self) -> None:
        """REVIEW_HOLD (specimen held for review) beats PROCEED (flag-and-proceed)."""
        assert SEVERITY_ORDER["REVIEW_HOLD"] < SEVERITY_ORDER["PROCEED"]

    def test_proceed_beats_accept(self) -> None:
        assert SEVERITY_ORDER["PROCEED"] < SEVERITY_ORDER["ACCEPT"]

    def test_is_immutable(self) -> None:
        with pytest.raises((TypeError, AttributeError)):
            SEVERITY_ORDER["NEW_KEY"] = 99  # type: ignore[index]


class TestSeverityLiteralParity:
    """Guard/pin that Severity Literal and SEVERITY_ORDER stay in sync.

    These are not red-first tests — both assertions pass today (by design).
    They pin the invariant so a future edit that adds a severity to the Literal
    but forgets to add it to SEVERITY_ORDER (or vice-versa) is caught immediately.
    """

    def test_severity_literal_keys_match_severity_order_keys(self) -> None:
        """Every member of the Severity Literal must appear in SEVERITY_ORDER and vice-versa."""
        assert set(get_args(Severity)) == set(SEVERITY_ORDER.keys())

    def test_severity_literal_order_agrees_with_severity_order_values(self) -> None:
        """The index of each Severity member in the Literal must equal its SEVERITY_ORDER value.

        The loader derives sort keys from SEVERITY_ORDER[r.severity]. The Literal
        order was historically used as the canonical rank source; pinning both
        directions here ensures they never silently diverge.
        """
        for idx, sev in enumerate(get_args(Severity)):
            assert SEVERITY_ORDER[sev] == idx, (
                f"Severity Literal position {idx} is {sev!r} but "
                f"SEVERITY_ORDER[{sev!r}] == {SEVERITY_ORDER[sev]}"
            )

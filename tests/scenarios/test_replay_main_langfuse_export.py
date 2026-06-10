"""Tests for Slice B: main routes Langfuse-on runs through replay_with_langfuse_export.

After the reroute, main always calls replay_with_langfuse_export (not
_replay_to_langfuse_with_release or replay_to_langfuse) when Langfuse is enabled.
The release value must be '{hex}-{safe_model_id}'.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import samantha_server.scenarios.replay as replay_module
from samantha_server.scenarios.replay import AccuracyReport, ScenarioVerdict, StepVerdict

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_deterministic_scenario(tmp_path: Path, scenario_id: str = "SC-B01") -> None:
    subdir = tmp_path / "rule_coverage"
    subdir.mkdir(parents=True, exist_ok=True)
    fixture = {
        "scenario_id": scenario_id,
        "category": "rule_coverage",
        "description": "Langfuse main() reroute test",
        "events": [
            {
                "step": 1,
                "event_type": "order_received",
                "event_data": {
                    "patient_name": "TEST, B",
                    "age": 45,
                    "sex": "F",
                    "specimen_type": "biopsy",
                    "anatomic_site": "breast",
                    "fixative": "formalin",
                    "fixation_time_hours": 24.0,
                    "ordered_tests": ["Breast IHC Panel"],
                    "priority": "routine",
                    "billing_info_present": True,
                },
                "expected_output": {
                    "next_state": "ACCEPTED",
                    "applied_rules": ["ACC-008"],
                    "flags": [],
                    "routing_path": "deterministic",
                },
            }
        ],
    }
    (subdir / f"{scenario_id.lower()}.json").write_text(json.dumps(fixture, indent=2))


def _make_pass_report() -> AccuracyReport:
    step = StepVerdict(
        scenario_id="SC-B01",
        step_index=1,
        status="pass",
        expected=replay_module.ExpectedOutput(
            next_state="ACCEPTED", applied_rules=("ACC-008",), flags=()
        ),
        predicted=replay_module.PredictedOutput(
            next_state="ACCEPTED", applied_rules=("ACC-008",), flags=()
        ),
    )
    verdict = ScenarioVerdict(
        scenario_id="SC-B01",
        category="rule_coverage",
        status="pass",
        step_verdicts=(step,),
    )
    return AccuracyReport(
        included_accuracy=1.0,
        overall_accuracy=1.0,
        included_total=1,
        overall_total=1,
        included_pass=1,
        overall_pass=1,
        scenario_verdicts=(verdict,),
        p99_latency_us=None,
        p99_latency_us_llm=None,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestMainUsesReplayWithLangfuseExport:
    """main() calls replay_with_langfuse_export when Langfuse is enabled."""

    def test_single_model_langfuse_on_calls_replay_with_langfuse_export(
        self, tmp_path: Path
    ) -> None:
        """Single-model + single-sweep + Langfuse on → replay_with_langfuse_export called once."""
        import samantha_server.config as cfg

        _write_deterministic_scenario(tmp_path)

        mock_export = MagicMock(return_value=(_make_pass_report(), ["a" * 32]))

        with (
            patch.object(cfg, "LANGFUSE_ENABLED", True),
            patch.object(cfg, "LANGFUSE_PUBLIC_KEY", "pk"),
            patch.object(cfg, "LANGFUSE_SECRET_KEY", "sk"),
            patch.object(cfg, "LANGFUSE_BASE_URL", "http://localhost:3000"),
            patch.object(replay_module, "replay_with_langfuse_export", mock_export),
        ):
            exit_code = replay_module.main([str(tmp_path), "--no-warm-up"])

        assert exit_code == 0
        assert mock_export.call_count == 1

    def test_single_model_langfuse_on_release_format(self, tmp_path: Path) -> None:
        """release passed to replay_with_langfuse_export matches '{hex}-{safe_model_id}'."""
        import samantha_server.config as cfg

        _write_deterministic_scenario(tmp_path)

        captured_releases: list[str] = []

        def capturing_export(*args: object, **kwargs: object) -> object:
            captured_releases.append(str(kwargs.get("release", "")))
            return (_make_pass_report(), ["a" * 32])

        model_id = "my-model"
        safe_model_id = model_id.replace(":", "_").replace("/", "_")

        with (
            patch.object(cfg, "LANGFUSE_ENABLED", True),
            patch.object(cfg, "LANGFUSE_PUBLIC_KEY", "pk"),
            patch.object(cfg, "LANGFUSE_SECRET_KEY", "sk"),
            patch.object(cfg, "LANGFUSE_BASE_URL", "http://localhost:3000"),
            patch.object(cfg, "LLM_MODEL_NAME", model_id),
            patch.object(replay_module, "replay_with_langfuse_export", capturing_export),
        ):
            replay_module.main([str(tmp_path), f"--models={model_id}", "--no-warm-up"])

        assert len(captured_releases) == 1
        release = captured_releases[0]
        # Format: {32-char hex}-{safe_model_id}
        assert release.endswith(f"-{safe_model_id}"), (
            f"release {release!r} must end with '-{safe_model_id}'"
        )
        hex_part = release[: -len(f"-{safe_model_id}")]
        assert re.fullmatch(r"[0-9a-f]{32}", hex_part), (
            f"hex prefix {hex_part!r} must be 32 hex chars"
        )

    def test_multi_model_langfuse_on_calls_export_per_model(self, tmp_path: Path) -> None:
        """Multi-model Langfuse-on → replay_with_langfuse_export called once per model."""
        import samantha_server.config as cfg

        _write_deterministic_scenario(tmp_path)

        captured_releases: list[str] = []

        def capturing_export(*args: object, **kwargs: object) -> object:
            captured_releases.append(str(kwargs.get("release", "")))
            return (_make_pass_report(), ["a" * 32])

        with (
            patch.object(cfg, "LANGFUSE_ENABLED", True),
            patch.object(cfg, "LANGFUSE_PUBLIC_KEY", "pk"),
            patch.object(cfg, "LANGFUSE_SECRET_KEY", "sk"),
            patch.object(cfg, "LANGFUSE_BASE_URL", "http://localhost:3000"),
            patch.object(replay_module, "replay_with_langfuse_export", capturing_export),
        ):
            replay_module.main([str(tmp_path), "--models=model-x,model-y", "--no-warm-up"])

        assert len(captured_releases) == 2
        # Each model gets a distinct release
        assert captured_releases[0] != captured_releases[1]
        assert "model-x" in captured_releases[0] or "model-y" in captured_releases[0]
        assert "model-x" in captured_releases[1] or "model-y" in captured_releases[1]

    def test_multi_sweep_langfuse_on_calls_export_per_sweep(self, tmp_path: Path) -> None:
        """Multi-sweep Langfuse-on → replay_with_langfuse_export called once per sweep."""
        import samantha_server.config as cfg

        _write_deterministic_scenario(tmp_path)

        call_count = [0]

        def counting_export(*args: object, **kwargs: object) -> object:
            call_count[0] += 1
            return (_make_pass_report(), ["a" * 32])

        with (
            patch.object(cfg, "LANGFUSE_ENABLED", True),
            patch.object(cfg, "LANGFUSE_PUBLIC_KEY", "pk"),
            patch.object(cfg, "LANGFUSE_SECRET_KEY", "sk"),
            patch.object(cfg, "LANGFUSE_BASE_URL", "http://localhost:3000"),
            patch.object(replay_module, "replay_with_langfuse_export", counting_export),
        ):
            replay_module.main([str(tmp_path), "--n-sweeps=3", "--no-warm-up"])

        assert call_count[0] == 3

    def test_release_sanitises_colon_in_model_id(self, tmp_path: Path) -> None:
        """langfuse.release uses '_' not ':' when model_id contains a colon."""
        import samantha_server.config as cfg

        _write_deterministic_scenario(tmp_path)

        captured_releases: list[str] = []

        def capturing_export(*args: object, **kwargs: object) -> object:
            captured_releases.append(str(kwargs.get("release", "")))
            return (_make_pass_report(), ["a" * 32])

        with (
            patch.object(cfg, "LANGFUSE_ENABLED", True),
            patch.object(cfg, "LANGFUSE_PUBLIC_KEY", "pk"),
            patch.object(cfg, "LANGFUSE_SECRET_KEY", "sk"),
            patch.object(cfg, "LANGFUSE_BASE_URL", "http://localhost:3000"),
            patch.object(replay_module, "replay_with_langfuse_export", capturing_export),
        ):
            replay_module.main(
                [
                    str(tmp_path),
                    "--models=test-org/test-model:q4,plain-model",
                    "--no-warm-up",
                ]
            )

        assert len(captured_releases) == 2
        for release in captured_releases:
            assert ":" not in release, f"release {release!r} must not contain ':'"

    def test_two_models_get_distinct_release_values(self, tmp_path: Path) -> None:
        """Two models in same run get distinct release values."""
        import samantha_server.config as cfg

        _write_deterministic_scenario(tmp_path)

        captured_releases: list[str] = []

        def capturing_export(*args: object, **kwargs: object) -> object:
            captured_releases.append(str(kwargs.get("release", "")))
            return (_make_pass_report(), ["a" * 32])

        with (
            patch.object(cfg, "LANGFUSE_ENABLED", True),
            patch.object(cfg, "LANGFUSE_PUBLIC_KEY", "pk"),
            patch.object(cfg, "LANGFUSE_SECRET_KEY", "sk"),
            patch.object(cfg, "LANGFUSE_BASE_URL", "http://localhost:3000"),
            patch.object(replay_module, "replay_with_langfuse_export", capturing_export),
        ):
            replay_module.main([str(tmp_path), "--models=model-x,model-y", "--no-warm-up"])

        assert captured_releases[0] != captured_releases[1]

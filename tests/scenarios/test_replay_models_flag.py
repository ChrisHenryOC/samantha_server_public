"""Tests for the --models <csv> flag in the replay CLI.

Verifies:
- main() with --models=stub1,stub2 runs replay once per model
- Two per-model report summaries appear in the output
- Combined cross-model summary is printed at the end
- Exit code is non-zero if any model fails the gate
- Exit code is 0 when all models pass
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_deterministic_scenario(tmp_path: Path, scenario_id: str = "SC-MODEL-01") -> None:
    """Write a minimal deterministic scenario that should pass."""
    subdir = tmp_path / "rule_coverage"
    subdir.mkdir(parents=True, exist_ok=True)
    fixture = {
        "scenario_id": scenario_id,
        "category": "rule_coverage",
        "description": "Simple pass scenario for model sweep",
        "events": [
            {
                "step": 1,
                "event_type": "order_received",
                "event_data": {
                    "patient_name": "TEST, Alice",
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
    path = subdir / f"{scenario_id.lower()}.json"
    path.write_text(json.dumps(fixture, indent=2))


def _make_stub_llm(model_id: str = "stub-model") -> MagicMock:
    from samantha_server.llm.client import LLMResponse

    mock = MagicMock()
    mock.model_id = model_id
    mock.complete.return_value = LLMResponse(
        text=f"Response from {model_id}",
        input_tokens=5,
        output_tokens=5,
        model_id=model_id,
        latency_us=100,
    )
    return mock


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestModelsFlag:
    def test_models_flag_runs_replay_twice(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """--models=stub1,stub2 causes two replay runs."""
        from samantha_server.scenarios.replay import main

        _write_deterministic_scenario(tmp_path)

        # Deterministic-only corpus needs no LLM client — no patching required.
        exit_code = main([str(tmp_path), "--models=stub1,stub2", "--no-warm-up"])

        assert exit_code == 0
        captured = capsys.readouterr()
        combined_output = captured.out + captured.err
        # Both model names should appear in the output
        assert "stub1" in combined_output
        assert "stub2" in combined_output

    def test_models_flag_shows_cross_model_summary(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """--models flag produces a cross-model summary section."""
        from samantha_server.scenarios.replay import main

        _write_deterministic_scenario(tmp_path)

        exit_code = main([str(tmp_path), "--models=alpha,beta", "--no-warm-up"])
        assert exit_code == 0

        captured = capsys.readouterr()
        combined_output = captured.out + captured.err
        # Expect a cross-model summary heading
        assert (
            "Cross-model" in combined_output
            or "Model summary" in combined_output
            or "Models" in combined_output
        )

    def test_models_flag_nonzero_exit_when_any_model_fails(self, tmp_path: Path) -> None:
        """Exit code is non-zero when any model fails the accuracy gate."""
        from samantha_server.scenarios.replay import main

        # Write a failing scenario (wrong expected state)
        subdir = tmp_path / "rule_coverage"
        subdir.mkdir(parents=True, exist_ok=True)
        failing_fixture = {
            "scenario_id": "SC-FAIL-01",
            "category": "rule_coverage",
            "description": "Scenario that fails",
            "events": [
                {
                    "step": 1,
                    "event_type": "order_received",
                    "event_data": {
                        "patient_name": "TEST, Bob",
                        "age": 55,
                        "sex": "M",
                        "specimen_type": "biopsy",
                        "anatomic_site": "breast",
                        "fixative": "formalin",
                        "fixation_time_hours": 24.0,
                        "ordered_tests": ["Breast IHC Panel"],
                        "priority": "routine",
                        "billing_info_present": True,
                    },
                    "expected_output": {
                        "next_state": "DO_NOT_PROCESS",  # Wrong — should be ACCEPTED
                        "applied_rules": ["ACC-008"],
                        "flags": [],
                        "routing_path": "deterministic",
                    },
                }
            ],
        }
        (subdir / "sc-fail-01.json").write_text(json.dumps(failing_fixture, indent=2))

        # A failing scenario with 1/1 failure gives 0% accuracy < 99.5% threshold.
        exit_code = main([str(tmp_path), "--models=m1,m2", "--no-warm-up"])
        assert exit_code != 0

    def test_models_flag_per_model_report_summary(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """Each model gets its own replay report section in the output."""
        from samantha_server.scenarios.replay import main

        _write_deterministic_scenario(tmp_path)

        exit_code = main([str(tmp_path), "--models=model-a,model-b", "--no-warm-up"])
        assert exit_code == 0

        captured = capsys.readouterr()
        combined_output = captured.out + captured.err
        # Each model should have its own report section
        assert "model-a" in combined_output
        assert "model-b" in combined_output

    def test_single_model_same_as_no_models_flag(self, tmp_path: Path) -> None:
        """--models=single produces same outcome as no --models (single replay run)."""
        from samantha_server.scenarios.replay import main

        _write_deterministic_scenario(tmp_path)

        exit_code_single = main([str(tmp_path), "--models=single-model", "--no-warm-up"])
        exit_code_none = main([str(tmp_path), "--no-warm-up"])
        assert exit_code_single == exit_code_none == 0

    def test_models_flag_shows_cross_model_summary_strict_heading(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """cross-model summary heading must contain 'Cross-model summary' verbatim."""
        from samantha_server.scenarios.replay import main

        _write_deterministic_scenario(tmp_path)

        exit_code = main([str(tmp_path), "--models=alpha,beta", "--no-warm-up"])
        assert exit_code == 0

        captured = capsys.readouterr()
        combined_output = captured.out + captured.err
        # Tighten: exact heading must appear
        assert "Cross-model summary" in combined_output, (
            f"Expected 'Cross-model summary' in output, got: {combined_output!r}"
        )


# ---------------------------------------------------------------------------
# M7: Per-model loop exception does not discard prior model results
# ---------------------------------------------------------------------------


class TestModelsLoopExceptionHandling:
    """M7: Exception in model B does not discard model A report; combined exit non-zero."""

    def test_exception_in_second_model_keeps_first_model_report(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """When model B raises during replay, model A's report is still printed; exit non-zero."""
        from unittest.mock import patch

        import samantha_server.scenarios.replay as replay_module

        _write_deterministic_scenario(tmp_path, "SC-M7-01")

        call_count = 0
        real_replay = replay_module.replay

        def raise_on_second_call(*args: object, **kwargs: object) -> object:
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("Model B replay failure")
            return real_replay(*args, **kwargs)

        with patch.object(replay_module, "replay", side_effect=raise_on_second_call):
            exit_code = replay_module.main(
                [str(tmp_path), "--models=model-a,model-b", "--no-warm-up"]
            )

        assert exit_code != 0, "Exit code must be non-zero when a model raises"

        captured = capsys.readouterr()
        combined = captured.out + captured.err
        # Model A's report should still appear (it ran successfully before the exception)
        assert "model-a" in combined, "Model A report must appear even when model B raises"
        # Model B failure must be reported in stderr
        assert "model-b" in combined.lower() or "Model B" in combined, (
            "Model B failure must be reported in output"
        )
        assert "Model B replay failure" in combined or "ERROR" in combined, (
            "Exception message or ERROR marker should appear in output"
        )


# ---------------------------------------------------------------------------
# M10: langfuse.release colon sanitisation
# ---------------------------------------------------------------------------


class TestLangfuseReleaseSanitisation:
    """M10: model_id with colon or slash is sanitised in langfuse.release."""

    def test_release_sanitises_colon_in_model_id(self, tmp_path: Path) -> None:
        """langfuse.release uses '_' not ':' as separator when model_id contains a colon."""
        from unittest.mock import patch

        import samantha_server.config as cfg
        import samantha_server.scenarios.replay as replay_module
        from samantha_server.scenarios.replay import AccuracyReport

        _write_deterministic_scenario(tmp_path, "SC-M10-01")

        _pass_report = AccuracyReport(
            included_accuracy=1.0,
            overall_accuracy=1.0,
            included_total=1,
            overall_total=1,
            included_pass=1,
            overall_pass=1,
            scenario_verdicts=(),
            p99_latency_us=None,
            p99_latency_us_llm=None,
        )

        captured_releases: list[str] = []

        def capturing_export(*args: object, **kwargs: object) -> object:
            captured_releases.append(str(kwargs.get("release", "")))
            return (_pass_report, ["a" * 32])

        with (
            patch.object(cfg, "LANGFUSE_ENABLED", True),
            patch.object(cfg, "LANGFUSE_PUBLIC_KEY", "pk"),
            patch.object(cfg, "LANGFUSE_SECRET_KEY", "sk"),
            patch.object(cfg, "LANGFUSE_BASE_URL", "http://localhost:3000"),
            patch.object(
                replay_module,
                "replay_with_langfuse_export",
                side_effect=capturing_export,
            ),
        ):
            # Use a model_id with a colon (common in model quantisation suffixes)
            # and a slash (common in org/repo notation). test-org is a synthetic
            # placeholder — not a real deployed model path.
            replay_module.main(
                [str(tmp_path), "--models=test-org/test-model:q4,plain-model", "--no-warm-up"]
            )

        assert len(captured_releases) == 2
        for release in captured_releases:
            assert ":" not in release, (
                f"release {release!r} must not contain ':' (sanitise model_id colons)"
            )


# ---------------------------------------------------------------------------
# M14: Per-model langfuse.release distinctness
# ---------------------------------------------------------------------------


class TestPerModelLangfuseReleaseDistinctness:
    """M14: Two models must produce distinct langfuse.release values."""

    def test_two_models_get_distinct_release_values(self, tmp_path: Path) -> None:
        """Each model run gets a distinct release label."""
        from unittest.mock import patch

        import samantha_server.config as cfg
        import samantha_server.scenarios.replay as replay_module
        from samantha_server.scenarios.replay import AccuracyReport

        _write_deterministic_scenario(tmp_path, "SC-M14-01")

        _pass_report = AccuracyReport(
            included_accuracy=1.0,
            overall_accuracy=1.0,
            included_total=1,
            overall_total=1,
            included_pass=1,
            overall_pass=1,
            scenario_verdicts=(),
            p99_latency_us=None,
            p99_latency_us_llm=None,
        )

        captured_releases: list[str] = []

        def capturing_export(*args: object, **kwargs: object) -> object:
            captured_releases.append(str(kwargs.get("release", "")))
            return (_pass_report, ["a" * 32])

        with (
            patch.object(cfg, "LANGFUSE_ENABLED", True),
            patch.object(cfg, "LANGFUSE_PUBLIC_KEY", "pk"),
            patch.object(cfg, "LANGFUSE_SECRET_KEY", "sk"),
            patch.object(cfg, "LANGFUSE_BASE_URL", "http://localhost:3000"),
            patch.object(
                replay_module,
                "replay_with_langfuse_export",
                side_effect=capturing_export,
            ),
        ):
            replay_module.main([str(tmp_path), "--models=model-x,model-y", "--no-warm-up"])

        assert len(captured_releases) == 2
        assert captured_releases[0] != captured_releases[1], (
            "Each model must get a distinct langfuse.release value"
        )
        # Release must contain model identifier
        assert "model-x" in captured_releases[0] or "model-y" in captured_releases[0]
        assert "model-x" in captured_releases[1] or "model-y" in captured_releases[1]

    def test_llm_client_not_built_on_deterministic_corpus_with_no_warmup(
        self, tmp_path: Path
    ) -> None:
        """With --no-warm-up, a deterministic-only corpus never calls
        _build_llm_client_for_replay.

        Default behavior (warm-up on) builds the client once per model to
        issue a cold-load preflight; --no-warm-up suppresses that.
        The deterministic-skip optimization remains intact when warm-up is
        opted out — the per-model loop still runs but no LLM client is
        constructed.
        """
        from unittest.mock import patch

        import samantha_server.scenarios.replay as replay_module

        _write_deterministic_scenario(tmp_path, "SC-M14-02")

        build_call_count = 0
        real_build = replay_module._build_llm_client_for_replay

        def counting_build() -> object:
            nonlocal build_call_count
            build_call_count += 1
            return real_build()

        with patch.object(
            replay_module, "_build_llm_client_for_replay", side_effect=counting_build
        ):
            exit_code = replay_module.main([str(tmp_path), "--models=m1,m2", "--no-warm-up"])

        assert exit_code == 0
        assert build_call_count == 0, (
            "Deterministic-only corpus + --no-warm-up must not build any LLM client"
        )

    def test_warmup_invokes_llm_per_sweep_per_model(self, tmp_path: Path) -> None:
        """Each (sweep, model) cell must receive its own warm-up LLM
        call, not just once-per-invocation. Once-per-invocation defends only
        sweep 1 model 1; under LRU eviction on memory-constrained hosts,
        every subsequent (sweep, model) iteration needs its own warm-up.

        Asserts N×M warm-up calls in (sweep, model) order: for `--models=A,B
        --n-sweeps=2` expect `[A, B, A, B]`.
        """
        from unittest.mock import patch

        import samantha_server.scenarios.replay as replay_module
        from samantha_server.llm.client import LLMResponse

        _write_deterministic_scenario(tmp_path, "SC-M14-03")

        warmup_models_in_order: list[str] = []

        def make_recording_client() -> MagicMock:
            mock = MagicMock()

            def _record(*_args: object, **_kw: object) -> LLMResponse:
                import samantha_server.config as _cfg

                warmup_models_in_order.append(_cfg.LLM_MODEL_NAME)
                return LLMResponse(
                    text="ok",
                    input_tokens=1,
                    output_tokens=1,
                    model_id=_cfg.LLM_MODEL_NAME,
                    latency_us=100,
                )

            # Only complete() is patched; warm-up calls client.complete()
            # directly. complete_json() is not used by warm-up so attaching
            # a side_effect there would mislead future readers (review #11).
            mock.complete.side_effect = _record
            return mock

        with patch.object(
            replay_module,
            "_build_llm_client_for_replay",
            side_effect=make_recording_client,
        ):
            exit_code = replay_module.main(
                [str(tmp_path), "--models=m-alpha,m-beta", "--n-sweeps=2"]
            )

        assert exit_code == 0
        assert warmup_models_in_order == ["m-alpha", "m-beta", "m-alpha", "m-beta"], (
            "Each (sweep, model) cell must be warmed; expect N×M calls in "
            f"(sweep, model) CLI order. Got: {warmup_models_in_order}"
        )

    def test_warmup_swallows_transport_error_and_continues(self, tmp_path: Path) -> None:
        """Warm-up failures of transport / runtime kind (LLMInferenceError,
        LLMTimeoutError, etc.) must NOT abort the sweep. The timed run is
        the authoritative surface for persistent issues — warm-up only
        forfeits its cold-load avoidance for that model.

        Pins the contract documented in `_warm_up_model`'s docstring (review
        #3). Without this test, a future refactor that narrowed the
        `except Exception` to a specific type could silently change the
        propagation contract.
        """
        from unittest.mock import MagicMock as _MM
        from unittest.mock import patch

        import samantha_server.scenarios.replay as replay_module
        from samantha_server.errors import LLMInferenceError

        _write_deterministic_scenario(tmp_path, "SC-M14-04")

        attempts: list[str] = []

        def make_raising_client() -> _MM:
            mock = _MM()
            mock.complete.side_effect = LLMInferenceError(model_id="m1", cause="transport")
            attempts.append("built")
            return mock

        with patch.object(
            replay_module,
            "_build_llm_client_for_replay",
            side_effect=make_raising_client,
        ):
            exit_code = replay_module.main([str(tmp_path), "--models=m1,m2"])

        # Both models attempted warm-up; sweep continued; deterministic
        # corpus has no LLM-path scenarios so the gate still passes.
        assert exit_code == 0
        assert len(attempts) == 2, f"Both models should have attempted warm-up; got {attempts}"

    def test_warmup_misconfigured_env_propagates(self, tmp_path: Path) -> None:
        """MisconfiguredEnvironmentError must propagate out of warm-up (and
        out of main()) rather than being swallowed by the `except Exception`
        branch (review #2).

        A broken `LLM_MODEL_PATH` or similar env-level config error affects
        every model in the survey; swallowing it produces a false-green on
        deterministic-only corpora where the sweep never re-tries the build.
        `_build_llm_client_for_replay()`'s docstring contract is "fail loud;
        no silent fallback" — this test pins that contract through the
        warm-up code path.
        """
        from unittest.mock import patch

        import samantha_server.scenarios.replay as replay_module
        from samantha_server.errors import MisconfiguredEnvironmentError

        _write_deterministic_scenario(tmp_path, "SC-M14-05")

        def raise_misconfigured() -> object:
            raise MisconfiguredEnvironmentError("fake LLM_MODEL_PATH for test")

        with (
            patch.object(
                replay_module,
                "_build_llm_client_for_replay",
                side_effect=raise_misconfigured,
            ),
            pytest.raises(MisconfiguredEnvironmentError),
        ):
            replay_module.main([str(tmp_path), "--models=m1,m2"])

    def test_warmup_runs_once_on_default_single_model(self, tmp_path: Path) -> None:
        """Without --models, warm-up must still run once on cfg.LLM_MODEL_NAME
        (the most common CI invocation; review #7).

        The default model is loaded into oMLX on first request just like any
        other; pre-loading it avoids a cold-load timeout on the first LLM-
        routed scenario.
        """
        from unittest.mock import patch

        import samantha_server.scenarios.replay as replay_module
        from samantha_server.llm.client import LLMResponse

        _write_deterministic_scenario(tmp_path, "SC-M14-06")

        warmup_models: list[str] = []

        def make_recording_client() -> MagicMock:
            mock = MagicMock()

            def _record(*_args: object, **_kw: object) -> LLMResponse:
                import samantha_server.config as _cfg

                warmup_models.append(_cfg.LLM_MODEL_NAME)
                return LLMResponse(
                    text="ok",
                    input_tokens=1,
                    output_tokens=1,
                    model_id=_cfg.LLM_MODEL_NAME,
                    latency_us=100,
                )

            mock.complete.side_effect = _record
            return mock

        with patch.object(
            replay_module,
            "_build_llm_client_for_replay",
            side_effect=make_recording_client,
        ):
            exit_code = replay_module.main([str(tmp_path)])

        import samantha_server.config as cfg

        assert exit_code == 0
        assert warmup_models == [cfg.LLM_MODEL_NAME], (
            "Single-model invocation must warm cfg.LLM_MODEL_NAME exactly once. "
            f"Got: {warmup_models}"
        )

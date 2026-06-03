"""Tests for samantha_server.errors — typed exception hierarchy."""

from pathlib import Path

import pytest


def test_samantha_error_is_importable() -> None:
    from samantha_server.errors import SamanthaError  # noqa: F401


def test_scenario_not_found_error_is_samantha_error_subclass() -> None:
    from samantha_server.errors import SamanthaError, ScenarioNotFoundError

    assert issubclass(ScenarioNotFoundError, SamanthaError)


def test_scenario_not_found_error_carries_scenario_id_attribute() -> None:
    from samantha_server.errors import ScenarioNotFoundError

    exc = ScenarioNotFoundError(scenario_id="qr-007")
    assert exc.scenario_id == "qr-007"


def test_scenario_not_found_error_includes_id_in_str() -> None:
    from samantha_server.errors import ScenarioNotFoundError

    exc = ScenarioNotFoundError(scenario_id="qr-007")
    assert "qr-007" in str(exc)


# ---------------------------------------------------------------------------
# ScenarioCorpusError
# ---------------------------------------------------------------------------


def test_scenario_corpus_empty_error_is_samantha_error_subclass() -> None:
    from samantha_server.errors import SamanthaError, ScenarioCorpusError

    assert issubclass(ScenarioCorpusError, SamanthaError)


def test_scenario_corpus_empty_error_is_importable() -> None:
    from samantha_server.errors import ScenarioCorpusError  # noqa: F401


def test_scenario_corpus_empty_error_carries_directory_attribute() -> None:
    from samantha_server.errors import ScenarioCorpusError

    d = Path("/some/dir")
    exc = ScenarioCorpusError(directory=d)
    assert exc.directory == d


def test_scenario_corpus_empty_error_carries_malformed_files_attribute() -> None:
    from samantha_server.errors import ScenarioCorpusError

    d = Path("/some/dir")
    bad = Path("/some/dir/bad.json")
    exc = ScenarioCorpusError(directory=d, malformed_files=(bad,))
    assert exc.malformed_files == (bad,)


def test_scenario_corpus_empty_error_malformed_files_defaults_to_empty_tuple() -> None:
    from samantha_server.errors import ScenarioCorpusError

    exc = ScenarioCorpusError(directory=Path("/some/dir"))
    assert exc.malformed_files == ()


def test_scenario_corpus_empty_error_positional_args_raise_type_error() -> None:
    from samantha_server.errors import ScenarioCorpusError

    with pytest.raises(TypeError):
        ScenarioCorpusError(Path("/some/dir"))  # type: ignore[misc]


def test_scenario_corpus_empty_error_str_includes_directory() -> None:
    from samantha_server.errors import ScenarioCorpusError

    d = Path("/some/dir")
    exc = ScenarioCorpusError(directory=d)
    assert "/some/dir" in str(exc)


def test_scenario_corpus_empty_error_str_includes_malformed_files_when_present() -> None:
    from samantha_server.errors import ScenarioCorpusError

    d = Path("/some/dir")
    bad = Path("/some/dir/bad.json")
    exc = ScenarioCorpusError(directory=d, malformed_files=(bad,))
    assert "bad.json" in str(exc)


# ---------------------------------------------------------------------------
# MisconfiguredEnvironmentError
# ---------------------------------------------------------------------------


def test_misconfigured_environment_error_is_importable() -> None:
    from samantha_server.errors import MisconfiguredEnvironmentError  # noqa: F401


def test_misconfigured_environment_error_is_samantha_error_subclass() -> None:
    from samantha_server.errors import MisconfiguredEnvironmentError, SamanthaError

    assert issubclass(MisconfiguredEnvironmentError, SamanthaError)


def test_misconfigured_environment_error_accepts_positional_message() -> None:
    from samantha_server.errors import MisconfiguredEnvironmentError

    exc = MisconfiguredEnvironmentError("RECEIPT_SIGNING_KEY is not set")
    assert "RECEIPT_SIGNING_KEY" in str(exc)


# ---------------------------------------------------------------------------
# LLMClientError
# ---------------------------------------------------------------------------


def test_llm_client_error_is_importable() -> None:
    from samantha_server.errors import LLMClientError  # noqa: F401


def test_llm_client_error_is_samantha_error_subclass() -> None:
    from samantha_server.errors import LLMClientError, SamanthaError

    assert issubclass(LLMClientError, SamanthaError)


def test_llm_client_error_accepts_positional_message() -> None:
    from samantha_server.errors import LLMClientError

    exc = LLMClientError("something went wrong")
    assert "something went wrong" in str(exc)


# ---------------------------------------------------------------------------
# LLMTimeoutError
# ---------------------------------------------------------------------------


def test_llm_timeout_error_is_importable() -> None:
    from samantha_server.errors import LLMTimeoutError  # noqa: F401


def test_llm_timeout_error_is_llm_client_error_subclass() -> None:
    from samantha_server.errors import LLMClientError, LLMTimeoutError

    assert issubclass(LLMTimeoutError, LLMClientError)


def test_llm_timeout_error_is_samantha_error_subclass() -> None:
    from samantha_server.errors import LLMTimeoutError, SamanthaError

    assert issubclass(LLMTimeoutError, SamanthaError)


def test_llm_timeout_error_carries_model_id_attribute() -> None:
    from samantha_server.errors import LLMTimeoutError

    exc = LLMTimeoutError(model_id="llama-3.2-3B", timeout_us=5_000_000)
    assert exc.model_id == "llama-3.2-3B"


def test_llm_timeout_error_carries_timeout_us_attribute() -> None:
    from samantha_server.errors import LLMTimeoutError

    exc = LLMTimeoutError(model_id="llama-3.2-3B", timeout_us=5_000_000)
    assert exc.timeout_us == 5_000_000


def test_llm_timeout_error_positional_args_raise_type_error() -> None:
    from samantha_server.errors import LLMTimeoutError

    with pytest.raises(TypeError):
        LLMTimeoutError("llama-3.2-3B", 5_000_000)  # type: ignore[misc]


def test_llm_timeout_error_str_includes_model_id() -> None:
    from samantha_server.errors import LLMTimeoutError

    exc = LLMTimeoutError(model_id="llama-3.2-3B", timeout_us=5_000_000)
    assert "llama-3.2-3B" in str(exc)


def test_llm_timeout_error_str_includes_timeout_us() -> None:
    from samantha_server.errors import LLMTimeoutError

    exc = LLMTimeoutError(model_id="llama-3.2-3B", timeout_us=5_000_000)
    assert "5000000" in str(exc)


# ---------------------------------------------------------------------------
# LLMModelLoadError
# ---------------------------------------------------------------------------


def test_llm_model_load_error_is_importable() -> None:
    from samantha_server.errors import LLMModelLoadError  # noqa: F401


def test_llm_model_load_error_is_llm_client_error_subclass() -> None:
    from samantha_server.errors import LLMClientError, LLMModelLoadError

    assert issubclass(LLMModelLoadError, LLMClientError)


def test_llm_model_load_error_is_samantha_error_subclass() -> None:
    """G19: every typed error is SamanthaError-rooted (PR #97 review H-02)."""
    from samantha_server.errors import LLMModelLoadError, SamanthaError

    assert issubclass(LLMModelLoadError, SamanthaError)


def test_llm_model_load_error_carries_model_path_attribute() -> None:
    from samantha_server.errors import LLMModelLoadError

    exc = LLMModelLoadError(model_path="/models/llama-3b", cause="file not found")
    assert exc.model_path == "/models/llama-3b"


def test_llm_model_load_error_carries_cause_attribute() -> None:
    from samantha_server.errors import LLMModelLoadError

    exc = LLMModelLoadError(model_path="/models/llama-3b", cause="file not found")
    assert exc.cause == "file not found"


def test_llm_model_load_error_positional_args_raise_type_error() -> None:
    from samantha_server.errors import LLMModelLoadError

    with pytest.raises(TypeError):
        LLMModelLoadError("/models/llama-3b", "file not found")  # type: ignore[misc]


def test_llm_model_load_error_str_includes_model_path() -> None:
    from samantha_server.errors import LLMModelLoadError

    exc = LLMModelLoadError(model_path="/models/llama-3b", cause="file not found")
    assert "/models/llama-3b" in str(exc)


def test_llm_model_load_error_str_includes_cause() -> None:
    from samantha_server.errors import LLMModelLoadError

    exc = LLMModelLoadError(model_path="/models/llama-3b", cause="file not found")
    assert "file not found" in str(exc)


# ---------------------------------------------------------------------------
# LLMInferenceError
# ---------------------------------------------------------------------------


def test_llm_inference_error_is_importable() -> None:
    from samantha_server.errors import LLMInferenceError  # noqa: F401


def test_llm_inference_error_is_llm_client_error_subclass() -> None:
    from samantha_server.errors import LLMClientError, LLMInferenceError

    assert issubclass(LLMInferenceError, LLMClientError)


def test_llm_inference_error_is_samantha_error_subclass() -> None:
    """G19: every typed error is SamanthaError-rooted (PR #97 review H-02)."""
    from samantha_server.errors import LLMInferenceError, SamanthaError

    assert issubclass(LLMInferenceError, SamanthaError)


def test_llm_inference_error_carries_model_id_attribute() -> None:
    from samantha_server.errors import LLMInferenceError

    exc = LLMInferenceError(model_id="llama-3.2-3B", cause="CUDA OOM")
    assert exc.model_id == "llama-3.2-3B"


def test_llm_inference_error_carries_cause_attribute() -> None:
    from samantha_server.errors import LLMInferenceError

    exc = LLMInferenceError(model_id="llama-3.2-3B", cause="CUDA OOM")
    assert exc.cause == "CUDA OOM"


def test_llm_inference_error_positional_args_raise_type_error() -> None:
    from samantha_server.errors import LLMInferenceError

    with pytest.raises(TypeError):
        LLMInferenceError("llama-3.2-3B", "CUDA OOM")  # type: ignore[misc]


def test_llm_inference_error_str_includes_model_id() -> None:
    from samantha_server.errors import LLMInferenceError

    exc = LLMInferenceError(model_id="llama-3.2-3B", cause="CUDA OOM")
    assert "llama-3.2-3B" in str(exc)


def test_llm_inference_error_str_includes_cause() -> None:
    from samantha_server.errors import LLMInferenceError

    exc = LLMInferenceError(model_id="llama-3.2-3B", cause="CUDA OOM")
    assert "CUDA OOM" in str(exc)


# ---------------------------------------------------------------------------
# PHIBoundaryError (Phase 2 Step 5; PR #98)
# ---------------------------------------------------------------------------


def test_phi_boundary_error_is_importable() -> None:
    from samantha_server.errors import PHIBoundaryError  # noqa: F401


def test_phi_boundary_error_is_samantha_error_subclass() -> None:
    from samantha_server.errors import PHIBoundaryError, SamanthaError

    assert issubclass(PHIBoundaryError, SamanthaError)


def test_phi_boundary_error_carries_age_attribute() -> None:
    from samantha_server.errors import PHIBoundaryError

    exc = PHIBoundaryError(age=90)
    assert exc.age == 90


def test_phi_boundary_error_positional_construction_raises_type_error() -> None:
    from samantha_server.errors import PHIBoundaryError

    with pytest.raises(TypeError):
        PHIBoundaryError(90)  # type: ignore[misc]


def test_phi_boundary_error_str_does_not_leak_offending_age() -> None:
    """H-01 from PR #98 review: the age integer is PHI under HIPAA Safe
    Harbor 45 CFR 164.514(b)(2)(i)(C). Forwarding ``str(exc)`` to log
    aggregators would leak the value, so the integer is deliberately
    omitted from the message — only the .age attribute carries it."""
    from samantha_server.errors import PHIBoundaryError

    exc = PHIBoundaryError(age=91)
    msg = str(exc)
    # The integer must NOT appear in the string representation
    assert "91" not in msg, f"PHI leak: age 91 found in PHIBoundaryError __str__: {msg!r}"
    # But the .age attribute must still carry it for catch-site access
    assert exc.age == 91


def test_phi_boundary_error_str_does_not_leak_age_92() -> None:
    """Second age integer to confirm H-01 fix is not just for one value."""
    from samantha_server.errors import PHIBoundaryError

    exc = PHIBoundaryError(age=92)
    assert "92" not in str(exc)
    assert exc.age == 92


def test_phi_boundary_error_str_references_hipaa_safe_harbor() -> None:
    """The message guides remediation by naming the standard."""
    from samantha_server.errors import PHIBoundaryError

    exc = PHIBoundaryError(age=90)
    assert "HIPAA Safe Harbor" in str(exc)


# ---------------------------------------------------------------------------
# ReceiptPersistenceError (Phase 2 Step 6)
# ---------------------------------------------------------------------------


def test_receipt_persistence_error_is_importable() -> None:
    from samantha_server.errors import ReceiptPersistenceError  # noqa: F401


def test_receipt_persistence_error_is_samantha_error_subclass() -> None:
    from samantha_server.errors import ReceiptPersistenceError, SamanthaError

    assert issubclass(ReceiptPersistenceError, SamanthaError)


def test_receipt_persistence_error_takes_cause_kwarg() -> None:
    from samantha_server.errors import ReceiptPersistenceError

    exc = ReceiptPersistenceError(cause="disk full")
    assert exc.cause == "disk full"


def test_receipt_persistence_error_str_includes_cause() -> None:
    from samantha_server.errors import ReceiptPersistenceError

    exc = ReceiptPersistenceError(cause="disk full")
    assert "disk full" in str(exc)


def test_receipt_persistence_error_positional_raises_type_error() -> None:
    from samantha_server.errors import ReceiptPersistenceError

    with pytest.raises(TypeError):
        ReceiptPersistenceError("disk full")  # type: ignore[misc]


# ---------------------------------------------------------------------------
# UndispatchedRuleError rebased onto SamanthaError (Phase 2 Step 6)
# ---------------------------------------------------------------------------


def test_undispatched_rule_error_is_samantha_error_subclass() -> None:
    from samantha_server.engine.evaluator import UndispatchedRuleError
    from samantha_server.errors import SamanthaError

    assert issubclass(UndispatchedRuleError, SamanthaError)


# ---------------------------------------------------------------------------
# SkillLoaderError rebased onto SamanthaError (Phase 2 Step 6)
# ---------------------------------------------------------------------------


def test_skill_loader_error_is_samantha_error_subclass() -> None:
    from samantha_server.errors import SamanthaError
    from samantha_server.skills.loader import SkillLoaderError

    assert issubclass(SkillLoaderError, SamanthaError)

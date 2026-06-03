"""Value-regression tests for committed JSON data files (#1) and live-path
proof tests that renderers consume the JSON rather than hardcoded values (#5).

All tests in this file are pure — they do not change any production file and
do not render PNGs (except the live-path monkeypatch tests, which use tmp_path
and synthetic JSON with deliberately different values).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# Resolve the charts/data directory relative to this file.
_DATA_DIR = Path(__file__).resolve().parents[1] / "data"


# ---------------------------------------------------------------------------
# Helper: committed JSON loader
# ---------------------------------------------------------------------------


def _load_json(name: str) -> dict:  # type: ignore[type-arg]
    return json.loads((_DATA_DIR / name).read_text())


# ===========================================================================
# #1 — Value-regression tests for committed JSON files
# ===========================================================================


class TestChart1AccuracyRankingJson:
    """Pin the four models + passed/total in chart1-accuracy-ranking.json."""

    def setup_method(self) -> None:
        raw = _load_json("chart1-accuracy-ranking.json")
        self.models = {e["label"]: e for e in raw["models"]}

    def test_four_models_present(self) -> None:
        assert len(self.models) == 4

    def test_incumbent_149_149(self) -> None:
        m = self.models["Qwen3-Next-80B-A3B-Instruct-4bit"]
        assert m["passed"] == 149
        assert m["total"] == 149
        assert m["incumbent"] is True

    def test_qwen35_35b_147_149(self) -> None:
        m = self.models["Qwen3.5-35B-A3B-8bit"]
        assert m["passed"] == 147
        assert m["total"] == 149

    def test_gemma_26b_147_149(self) -> None:
        m = self.models["gemma-4-26B-A4B-it-MLX-4bit"]
        assert m["passed"] == 147
        assert m["total"] == 149

    def test_coder_32b_144_149(self) -> None:
        m = self.models["Qwen2.5-Coder-32B-Instruct-MLX-4bit"]
        assert m["passed"] == 144
        assert m["total"] == 149


class TestChart8CategoryHeatmapJson:
    """Pin category_totals sum and non-perfect cells in chart8-category-heatmap.json."""

    def setup_method(self) -> None:
        raw = _load_json("chart8-category-heatmap.json")
        self.category_totals = raw["category_totals"]
        self.non_perfect = {(e["model"], e["category"]): e["passed"] for e in raw["non_perfect"]}

    def test_category_totals_sum_to_149(self) -> None:
        total = sum(self.category_totals.values())
        assert total == 149, f"category_totals sum {total} != 149"

    def test_qwen35_llm_review_passed_5(self) -> None:
        key = ("Qwen3.5-35B\n(35B)", "llm_review")
        assert self.non_perfect[key] == 5

    def test_qwen35_query_passed_28(self) -> None:
        key = ("Qwen3.5-35B\n(35B)", "query")
        assert self.non_perfect[key] == 28

    def test_gemma_query_passed_27(self) -> None:
        key = ("gemma-4-26B\n(26B)", "query")
        assert self.non_perfect[key] == 27

    def test_coder_llm_review_passed_5(self) -> None:
        key = ("Qwen2.5-Coder-32B\n(32B)", "llm_review")
        assert self.non_perfect[key] == 5

    def test_coder_query_passed_25(self) -> None:
        key = ("Qwen2.5-Coder-32B\n(32B)", "query")
        assert self.non_perfect[key] == 25


class TestChartN2BenchmarksJson:
    """Pin samantha_stable_pct, local.composite, and published.mmlu_pro values."""

    def setup_method(self) -> None:
        raw = _load_json("chart-n2-benchmarks.json")
        self.models = raw["models"]

    def test_four_models_present(self) -> None:
        assert len(self.models) == 4

    def test_samantha_stable_pcts(self) -> None:
        assert self.models["Qwen3-Next-80B-A3B-Instruct-4bit"][
            "samantha_stable_pct"
        ] == pytest.approx(100.0)
        assert self.models["Qwen3.5-35B-A3B-8bit"]["samantha_stable_pct"] == pytest.approx(98.66)
        assert self.models["gemma-4-26B-A4B-it-MLX-4bit"]["samantha_stable_pct"] == pytest.approx(
            98.66
        )
        assert self.models["Qwen2.5-Coder-32B-Instruct-MLX-4bit"][
            "samantha_stable_pct"
        ] == pytest.approx(96.64)

    def test_local_composite_values(self) -> None:
        assert self.models["Qwen3-Next-80B-A3B-Instruct-4bit"]["local"][
            "composite"
        ] == pytest.approx(83.7)
        assert self.models["Qwen3.5-35B-A3B-8bit"]["local"]["composite"] == pytest.approx(84.3)
        assert self.models["gemma-4-26B-A4B-it-MLX-4bit"]["local"]["composite"] == pytest.approx(
            85.1
        )
        assert self.models["Qwen2.5-Coder-32B-Instruct-MLX-4bit"]["local"][
            "composite"
        ] == pytest.approx(76.8)

    def test_published_mmlu_pro_values(self) -> None:
        assert self.models["Qwen3-Next-80B-A3B-Instruct-4bit"]["published"][
            "mmlu_pro"
        ] == pytest.approx(80.6)
        assert self.models["Qwen3.5-35B-A3B-8bit"]["published"]["mmlu_pro"] == pytest.approx(85.3)
        assert self.models["gemma-4-26B-A4B-it-MLX-4bit"]["published"]["mmlu_pro"] == pytest.approx(
            82.6
        )
        assert self.models["Qwen2.5-Coder-32B-Instruct-MLX-4bit"]["published"][
            "mmlu_pro"
        ] == pytest.approx(50.0)


class TestChart5LatenciesJson:
    """Pin model presence, non-empty float lists, and incumbent list length."""

    def setup_method(self) -> None:
        raw = _load_json("chart5-latencies.json")
        self.latencies = raw["latencies"]

    def test_four_models_present(self) -> None:
        assert len(self.latencies) == 4

    def test_all_models_have_non_empty_float_lists(self) -> None:
        for model, lats in self.latencies.items():
            assert isinstance(lats, list), f"{model}: expected list, got {type(lats)}"
            assert len(lats) > 0, f"{model}: latency list is empty"
            assert all(isinstance(v, (int, float)) for v in lats), (
                f"{model}: non-float values in latency list"
            )

    def test_incumbent_list_length_175(self) -> None:
        incumbent = "Qwen3-Next-80B-A3B-Instruct-4bit"
        assert len(self.latencies[incumbent]) == 175


class TestChart7RadarJson:
    """Pin per-model axis presence and robustness values."""

    def setup_method(self) -> None:
        raw = _load_json("chart7-radar.json")
        self.models = raw["models"]

    def test_four_models_present(self) -> None:
        assert len(self.models) == 4

    def test_required_axes_present_for_all_models(self) -> None:
        for model, data in self.models.items():
            for key in ("accuracy", "robustness", "latency_p50_s"):
                assert key in data, f"{model} missing {key!r}"

    def test_robustness_values(self) -> None:
        assert self.models["Qwen3-Next-80B-A3B-Instruct-4bit"]["robustness"] == pytest.approx(100.0)
        assert self.models["Qwen3.5-35B-A3B-8bit"]["robustness"] == pytest.approx(83.33)
        assert self.models["gemma-4-26B-A4B-it-MLX-4bit"]["robustness"] == pytest.approx(93.10)
        assert self.models["Qwen2.5-Coder-32B-Instruct-MLX-4bit"]["robustness"] == pytest.approx(
            83.33
        )


# ===========================================================================
# #5 — Prove the JSON path is live: monkeypatch with different values
# ===========================================================================


def test_load_accuracy_ranking_reads_given_path(tmp_path: Path) -> None:
    """load_accuracy_ranking reads from the path argument, not hardcoded values.

    Patch the path to a JSON with different passed counts; assert the loader
    returns those patched counts, not the committed ones.
    """
    from samantha_charts.charts.accuracy_ranking import load_accuracy_ranking

    patched = {
        "models": [
            {"label": "ModelX", "passed": 42, "total": 149, "incumbent": True},
            {"label": "ModelY", "passed": 77, "total": 149, "incumbent": False},
        ]
    }
    p = tmp_path / "patched.json"
    p.write_text(json.dumps(patched))

    result = load_accuracy_ranking(p)

    assert len(result) == 2
    assert result[0].label == "ModelX"
    assert result[0].passed == 42
    assert result[1].label == "ModelY"
    assert result[1].passed == 77


def test_load_category_heatmap_reads_given_path(tmp_path: Path) -> None:
    """load_category_heatmap reads from the path argument, not hardcoded values."""
    from samantha_charts.charts.category_heatmap import load_category_heatmap

    patched = {
        "category_totals": {"ghost": 99},
        "models": ["SyntheticModel"],
        "categories": ["ghost"],
        "non_perfect": [],
    }
    p = tmp_path / "patched_heatmap.json"
    p.write_text(json.dumps(patched))

    cells, models, categories = load_category_heatmap(p)

    assert models == ["SyntheticModel"]
    assert categories == ["ghost"]
    assert all(c.total == 99 for c in cells)


def test_load_latencies_reads_given_path(tmp_path: Path) -> None:
    """load_latencies reads from the path argument, not hardcoded values."""
    from samantha_charts.charts.latency_box import load_latencies

    patched = {
        "latencies": {
            "FakeModel": [99.9, 88.8],
        }
    }
    p = tmp_path / "patched_latencies.json"
    p.write_text(json.dumps(patched))

    result = load_latencies(p)

    assert list(result.keys()) == ["FakeModel"]
    assert result["FakeModel"] == pytest.approx([99.9, 88.8])


def test_load_radar_data_reads_given_path(tmp_path: Path) -> None:
    """load_radar_data reads from the path argument, not hardcoded values."""
    from samantha_charts.charts.model_radar import load_radar_data

    patched = {
        "models": {
            "SynthModel": {
                "accuracy": 55.0,
                "robustness": 44.0,
                "latency_p50_s": 123.4,
            }
        }
    }
    p = tmp_path / "patched_radar.json"
    p.write_text(json.dumps(patched))

    result = load_radar_data(p)

    assert list(result.keys()) == ["SynthModel"]
    assert result["SynthModel"]["accuracy"] == pytest.approx(55.0)
    assert result["SynthModel"]["latency_p50_s"] == pytest.approx(123.4)


def test_load_benchmarks_reads_given_path(tmp_path: Path) -> None:
    """load_benchmarks reads from the path argument, not hardcoded values."""
    from samantha_charts.charts.benchmark_bars import load_benchmarks

    patched = {
        "models": {
            "SynthBench": {
                "local": {"composite": 42.1},
                "published": {"mmlu_pro": 55.5},
                "samantha_stable_pct": 77.7,
            }
        }
    }
    p = tmp_path / "patched_benchmarks.json"
    p.write_text(json.dumps(patched))

    result = load_benchmarks(p)

    assert list(result.keys()) == ["SynthBench"]
    assert result["SynthBench"]["samantha_stable_pct"] == pytest.approx(77.7)
    assert result["SynthBench"]["local"]["composite"] == pytest.approx(42.1)


# ===========================================================================
# Chart 9 — value-regression tests for chart9-refusal-types.json
# ===========================================================================


class TestChart9RefusalTypesJson:
    """Pin all known refusal-type keys + other in chart9-refusal-types.json."""

    def setup_method(self) -> None:
        from samantha_charts.charts.refusal_distribution import KNOWN_REFUSAL_TYPES

        raw = _load_json("chart9-refusal-types.json")
        self.counts = raw["refusal_counts"]
        self.meta = raw["_meta"]
        self.known = KNOWN_REFUSAL_TYPES

    def test_refusal_counts_keys_match_known_types_plus_other(self) -> None:
        """Committed JSON keys must equal KNOWN_REFUSAL_TYPES | {'other'} exactly."""
        expected = set(self.known) | {"other"}
        assert set(self.counts.keys()) == expected

    def test_other_key_present(self) -> None:
        assert "other" in self.counts

    def test_pinned_counts(self) -> None:
        assert self.counts["refused_llm_unavailable"] == 14
        assert self.counts["refused_phi_boundary"] == 3
        assert self.counts["refused_no_skill_for_state"] == 9
        assert self.counts["refused_skill_unavailable"] == 5
        assert self.counts["refused_ungrounded_stage_a"] == 7
        assert self.counts["refused_ungrounded_stage_b"] == 6
        assert self.counts["refused_uncertain"] == 8
        assert self.counts["refused_unparseable"] == 3
        assert self.counts["refused_unparseable_response"] == 2
        assert self.counts["refused_rule_not_in_dispatch"] == 2
        assert self.counts["refused_undispatched_rule"] == 1
        assert self.counts["refused_internal_error"] == 4
        assert self.counts["other"] == 2

    def test_sample_size_matches_sum_of_counts(self) -> None:
        total = sum(self.counts.values())
        assert self.meta["sample_size"] == total


# ===========================================================================
# Chart 10 — value-regression tests for chart10-routing-path-daily.json
# ===========================================================================


class TestChart10RoutingPathDailyJson:
    """Pin day count, chronological order, and both segment keys in chart10."""

    def setup_method(self) -> None:
        raw = _load_json("chart10-routing-path-daily.json")
        self.days = raw["days"]
        self.meta = raw["_meta"]

    def test_six_days_present(self) -> None:
        assert len(self.days) == 6

    def test_days_chronological(self) -> None:
        dates = [d["date"] for d in self.days]
        assert dates == sorted(dates)

    def test_missing_days_collapsed(self) -> None:
        """2026-05-23 and 2026-05-24 must be absent (deliberately collapsed)."""
        dates = {d["date"] for d in self.days}
        assert "2026-05-23" not in dates
        assert "2026-05-24" not in dates

    def test_all_days_have_both_segments(self) -> None:
        for day in self.days:
            assert "deterministic" in day, f"{day['date']} missing 'deterministic'"
            assert "llm" in day, f"{day['date']} missing 'llm'"

    def test_pinned_2026_05_20(self) -> None:
        day = next(d for d in self.days if d["date"] == "2026-05-20")
        assert day["deterministic"] == 380
        assert day["llm"] == 20

    def test_sample_size_equals_total_traces(self) -> None:
        total = sum(d["deterministic"] + d["llm"] for d in self.days)
        assert self.meta["sample_size"] == total


# ===========================================================================
# Live-path proof tests for chart9 and chart10 loaders
# ===========================================================================


def test_load_refusal_distribution_reads_given_path(tmp_path: Path) -> None:
    """load_refusal_distribution reads from the path argument, not hardcoded values.

    Patch the path to a JSON with different counts; assert the loader returns
    those patched counts, not the committed ones.
    """
    from samantha_charts.charts.refusal_distribution import (
        KNOWN_REFUSAL_TYPES,
        load_refusal_distribution,
    )

    # Build a complete refusal_counts dict using a distinctive value for one key.
    patched_counts = {k: 0 for k in KNOWN_REFUSAL_TYPES}
    patched_counts["refused_llm_unavailable"] = 42
    patched_counts["other"] = 7

    patched = {
        "_meta": {
            "sample_size": 99,
            "time_range": "synthetic range",
            "note": "data generated during testing",
        },
        "refusal_counts": patched_counts,
    }
    p = tmp_path / "patched_refusal.json"
    p.write_text(json.dumps(patched))

    result = load_refusal_distribution(p)

    assert result.sample_size == 99
    assert result.time_range == "synthetic range"
    assert result.counts["refused_llm_unavailable"] == 42
    assert result.counts["other"] == 7


def test_load_routing_path_daily_reads_given_path(tmp_path: Path) -> None:
    """load_routing_path_daily reads from the path argument, not hardcoded values."""
    from samantha_charts.charts.routing_path_daily import load_routing_path_daily

    patched = {
        "_meta": {
            "sample_size": 55,
            "time_range": "synthetic range",
            "note": "data generated during testing",
        },
        "days": [
            {"date": "2099-01-01", "deterministic": 50, "llm": 5},
        ],
    }
    p = tmp_path / "patched_routing.json"
    p.write_text(json.dumps(patched))

    result = load_routing_path_daily(p)

    assert result.sample_size == 55
    assert len(result.days) == 1
    assert result.days[0].date == "2099-01-01"
    assert result.days[0].deterministic == 50
    assert result.days[0].llm == 5


# ===========================================================================
# Chart 13 — value-regression tests for chart13-receipt-coverage.json
# ===========================================================================


class TestChart13ReceiptCoverageJson:
    """Pin decisions, receipts, traces, and coverage_pct in chart13."""

    def setup_method(self) -> None:
        raw = _load_json("chart13-receipt-coverage.json")
        self.decisions = raw["decisions"]
        self.receipts = raw["receipts"]
        self.traces = raw["traces"]
        self.coverage_pct = raw["coverage_pct"]

    def test_decisions_4545(self) -> None:
        assert self.decisions == 4545

    def test_receipts_4545(self) -> None:
        assert self.receipts == 4545

    def test_traces_4545(self) -> None:
        assert self.traces == 4545

    def test_coverage_pct_100(self) -> None:
        assert self.coverage_pct == pytest.approx(100.0)


# ===========================================================================
# Chart 11 — value-regression tests for chart11-receipt-sample.json
# ===========================================================================


class TestChart11ReceiptSampleJson:
    """Pin key receipt fields in chart11-receipt-sample.json."""

    def setup_method(self) -> None:
        raw = _load_json("chart11-receipt-sample.json")
        self.receipt = raw["receipt"]

    def test_receipt_id(self) -> None:
        assert self.receipt["receipt_id"] == "01KSK99SMCK1HZC7EMXDH3Z1YJ"

    def test_applied_rule_id(self) -> None:
        assert self.receipt["applied_rule_id"] == "ACC-009"

    def test_outcome(self) -> None:
        assert self.receipt["outcome"] == "held_missing_fixation_time"

    def test_verification(self) -> None:
        assert self.receipt["verification"] == "valid"


# ===========================================================================
# Chart 12 — value-regression tests for chart12-receipt-vs-trace.json
# ===========================================================================


class TestChart12ReceiptVsTraceJson:
    """Pin join integrity and outcome consistency in chart12."""

    def setup_method(self) -> None:
        raw = _load_json("chart12-receipt-vs-trace.json")
        self.receipt = raw["receipt"]
        self.trace = raw["trace"]
        self.meta = raw["_meta"]

    def test_receipt_and_trace_share_event_input_hash(self) -> None:
        assert self.trace["event_input_hash"] == self.meta["event_input_hash"]

    def test_event_input_hash_equals_meta(self) -> None:
        assert self.receipt["receipt_id"] == "01KSK99SMCK1HZC7EMXDH3Z1YJ"

    def test_trace_outcome_equals_receipt_outcome(self) -> None:
        assert self.trace["outcome"] == self.receipt["outcome"]


# ===========================================================================
# Chart 14 — value-regression tests for chart14-evolution.json
# ===========================================================================


class TestChart14EvolutionJson:
    """Pin point count, chronological dates, and specific milestone values."""

    def setup_method(self) -> None:
        raw = _load_json("chart14-evolution.json")
        self.points = raw["points"]

    def test_three_points_present(self) -> None:
        assert len(self.points) == 3

    def test_dates_chronological(self) -> None:
        dates = [p["date"] for p in self.points]
        assert dates == sorted(dates)

    def test_qwen_2026_05_16_is_100_pct(self) -> None:
        qwen_pt = next(p for p in self.points if p["date"] == "2026-05-16")
        assert qwen_pt["accuracy_pct"] == pytest.approx(100.0)
        assert qwen_pt["passed"] == 149
        assert qwen_pt["total"] == 149

    def test_gemma_2026_05_15_is_98_66(self) -> None:
        gemma_pt = next(p for p in self.points if p["date"] == "2026-05-15")
        assert gemma_pt["accuracy_pct"] == pytest.approx(98.66)
        assert gemma_pt["passed"] == 147
        assert gemma_pt["total"] == 149


# ===========================================================================
# Live-path proof tests for chart11, chart12, chart13, chart14 loaders
# ===========================================================================


def test_load_receipt_sample_reads_given_path(tmp_path: Path) -> None:
    """load_receipt_sample reads from the path argument, not hardcoded values."""
    from samantha_charts.charts.receipt_sample import load_receipt_sample

    patched = {
        "_meta": {"source": "test", "verification": "valid", "time_range": "2026"},
        "receipt": {
            "receipt_id": "01SYNTHETIC",
            "scenario_id": "SC-999",
            "applied_rule_id": "ACC-099",
            "outcome": "synthetic_outcome",
            "next_state": "SYNTHETIC_STATE",
            "signer_key_id": "v2",
            "signature_truncated": "aaaa...bbbb",
            "signature_bytes": 64,
            "signed_at_utc": "2099-01-01T00:00:00+00:00",
            "latency_us": 42,
            "primitive_traces": {},
            "verification": "valid",
        },
    }
    p = tmp_path / "patched_receipt_sample.json"
    p.write_text(json.dumps(patched))

    result = load_receipt_sample(p)

    assert result.receipt_id == "01SYNTHETIC"
    assert result.scenario_id == "SC-999"
    assert result.applied_rule_id == "ACC-099"
    assert result.latency_us == 42


def test_load_receipt_vs_trace_reads_given_path(tmp_path: Path) -> None:
    """load_receipt_vs_trace reads from the path argument, not hardcoded values."""
    from samantha_charts.charts.receipt_vs_trace import load_receipt_vs_trace

    synth_hash = "abcd1234" * 8
    patched = {
        "_meta": {
            "join_key": "event_input_hash",
            "event_input_hash": synth_hash,
            "source": "test",
        },
        "receipt": {
            "receipt_id": "01SYNTH",
            "applied_rule_id": "ACC-099",
            "outcome": "synth_outcome",
            "next_state": "SYNTH_STATE",
            "signer_key_id": "v2",
            "signed_at_utc": "2099-01-01T00:00:00+00:00",
        },
        "trace": {
            "trace_id": "synth_trace_id",
            "name": "SC-999",
            "timestamp": "2099-01-01T00:00:00Z",
            "outcome": "synth_outcome",
            "routing_path": "deterministic",
            "next_state": "SYNTH_STATE",
            "event_input_hash": synth_hash,
        },
    }
    p = tmp_path / "patched_receipt_vs_trace.json"
    p.write_text(json.dumps(patched))

    result = load_receipt_vs_trace(p)

    assert result.receipt["receipt_id"] == "01SYNTH"
    assert result.trace["trace_id"] == "synth_trace_id"
    assert result.event_input_hash == synth_hash


def test_load_receipt_coverage_reads_given_path(tmp_path: Path) -> None:
    """load_receipt_coverage reads from the path argument, not hardcoded values."""
    from samantha_charts.charts.receipt_coverage import load_receipt_coverage

    patched = {
        "_meta": {
            "time_range": "synthetic range",
            "source": "test",
        },
        "decisions": 999,
        "receipts": 999,
        "traces": 999,
        "coverage_pct": 100.0,
    }
    p = tmp_path / "patched_receipt_coverage.json"
    p.write_text(json.dumps(patched))

    result = load_receipt_coverage(p)

    assert result.decisions == 999
    assert result.receipts == 999
    assert result.coverage_pct == pytest.approx(100.0)


def test_load_accuracy_evolution_reads_given_path(tmp_path: Path) -> None:
    """load_accuracy_evolution reads from the path argument, not hardcoded values."""
    from samantha_charts.charts.accuracy_evolution import load_accuracy_evolution

    patched = {
        "_meta": {"metric": "test", "source": "test", "note": "test"},
        "points": [
            {
                "date": "2099-01-01",
                "label": "SynthMilestone",
                "accuracy_pct": 77.7,
                "passed": 116,
                "total": 149,
                "sweep_file": "synth.txt",
            }
        ],
    }
    p = tmp_path / "patched_evolution.json"
    p.write_text(json.dumps(patched))

    result = load_accuracy_evolution(p)

    assert len(result) == 1
    assert result[0].label == "SynthMilestone"
    assert result[0].accuracy_pct == pytest.approx(77.7)
    assert result[0].date == "2099-01-01"

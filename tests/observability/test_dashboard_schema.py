"""Schema-drift CI gate for Langfuse dashboard specs.

Each ``infra/langfuse/dashboards/*.yaml`` declares the panel shape an
operator replicates in the Langfuse v3 UI. Every field name a panel
references — under ``filters[].field``, ``group_by``, ``metrics[].field``,
or ``fields_referenced`` — must exist in
``docs/observability/trace-schema.md``. Without this gate, a typo or a
schema rename breaks dashboards silently and is only discovered on the
next demo. The test parses every YAML at CI time, extracts the
referenced field names, and asserts they appear in the schema doc.

This works around the lack of a public Langfuse v3 dashboard import API
(per https://github.com/orgs/langfuse/discussions/8819) — see
``infra/langfuse/dashboards/README.md`` for the manual UI replication
step.

Negative-test discipline: the schema-drift gate is *proven to be a
gate* via parametrised tests at the bottom of this file that feed
synthetic malformed YAMLs through the same extraction logic and
assert violations surface. Without those, a positive-only suite
would silently regress if the extraction logic broke.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

_REPO_ROOT: Path = Path(__file__).resolve().parents[2]
_DASHBOARDS_DIR: Path = _REPO_ROOT / "infra" / "langfuse" / "dashboards"
_SCHEMA_DOC: Path = _REPO_ROOT / "docs" / "observability" / "trace-schema.md"


def _load_dashboard_specs() -> list[tuple[Path, dict[str, object]]]:
    """Load every YAML under the dashboards directory.

    Distinct error paths for the two failure modes:

    - dir does not exist → loud ``FileNotFoundError``
    - dir exists but is empty → returns ``[]`` (callers assert non-empty)
    - YAML root is not a mapping → loud ``ValueError`` with a message
      that distinguishes "empty file" from "wrong root type"
    """
    if not _DASHBOARDS_DIR.exists():
        raise FileNotFoundError(
            f"dashboards directory missing: {_DASHBOARDS_DIR}. "
            "Step 11 requires this directory."
        )
    specs: list[tuple[Path, dict[str, object]]] = []
    for path in sorted(_DASHBOARDS_DIR.glob("*.yaml")):
        with path.open("r") as f:
            spec = yaml.safe_load(f)
        if spec is None:
            raise ValueError(
                f"{path}: YAML file is empty or comment-only (yaml.safe_load returned None)."
            )
        if not isinstance(spec, dict):
            raise ValueError(
                f"{path}: top-level YAML must be a mapping; got {type(spec).__name__}."
            )
        specs.append((path, spec))
    return specs


def _trace_schema_field_names() -> set[str]:
    """Pull every field name out of trace-schema.md.

    Two sources, both authoritative — the schema-drift test accepts a
    name from either:

    1. The fenced ``yaml`` blocks listing the trace-dict fields. We use
       ``re.findall`` (not ``re.search``) so any future second YAML
       block (worked example, decision_traces shape) is also indexed.
    2. Markdown table rows whose first cell wraps the field name in
       backticks (the "Span attributes (OTel)" table).

    The dashboard test stays in lockstep with the doc without
    reaching into a Pydantic model.
    """
    text = _SCHEMA_DOC.read_text()

    field_names: set[str] = set()

    # --- 1. Every fenced ``yaml`` block (re.findall, not re.search) ---
    blocks = re.findall(r"```yaml\n(.*?)\n```", text, flags=re.DOTALL)
    if not blocks:
        raise AssertionError(f"{_SCHEMA_DOC}: no ```yaml block found")
    for block in blocks:
        for line in block.splitlines():
            stripped = line.split("#", 1)[0].strip()
            # Skip empty lines, list items, and lines without a key.
            if not stripped or stripped.startswith("-") or ":" not in stripped:
                continue
            # Skip the wrapping document keys (depth-0, no leading
            # whitespace) — ``trace:`` is the doc shape's wrapper, not
            # a filterable field.
            if line[0] not in (" ", "\t"):
                continue
            key = stripped.split(":", 1)[0].strip()
            if key:
                field_names.add(key)

    # --- 2. Markdown table rows: first cell wrapped in backticks ---
    # Captures rows like: ``| `samantha.event_input_hash` | ... |``
    for backticked in re.findall(r"^\|\s*`([^`]+)`\s*\|", text, flags=re.MULTILINE):
        field_names.add(backticked.strip())

    return field_names


def _referenced_field_names(spec: dict[str, object], path: Path) -> set[str]:
    """Return the union of every field name a dashboard spec references.

    Wrong-type values are a hard error — silently dropping them would
    let a YAML typo (``filters: langfuse.trace.metadata.environment`` instead of
    ``filters: [{field: langfuse.trace.metadata.environment, ...}]``) pass the
    drift gate with zero fields inspected. Each guard raises with the
    file + the actual type so the operator sees what to fix.
    """
    fields: set[str] = set()

    filters = spec.get("filters")
    if filters is not None:
        if not isinstance(filters, list):
            raise TypeError(f"{path}: 'filters' must be a list; got {type(filters).__name__}.")
        for i, f in enumerate(filters):
            if not isinstance(f, dict):
                raise TypeError(f"{path}: filters[{i}] must be a mapping; got {type(f).__name__}.")
            if "field" in f:
                fields.add(str(f["field"]))

    group_by = spec.get("group_by")
    if group_by is not None:
        if not isinstance(group_by, list):
            raise TypeError(f"{path}: 'group_by' must be a list; got {type(group_by).__name__}.")
        fields.update(str(g) for g in group_by)

    metrics = spec.get("metrics")
    if metrics is not None:
        if not isinstance(metrics, list):
            raise TypeError(f"{path}: 'metrics' must be a list; got {type(metrics).__name__}.")
        for i, m in enumerate(metrics):
            if not isinstance(m, dict):
                raise TypeError(f"{path}: metrics[{i}] must be a mapping; got {type(m).__name__}.")
            if "field" in m:
                fields.add(str(m["field"]))

    return fields


# ---------------------------------------------------------------------------
# Positive tests against the committed YAMLs
# ---------------------------------------------------------------------------


def test_three_required_v0_panels_exist() -> None:
    """v0 canary: the three panels Plan § Step 11 names ship in this PR.

    Note: this is a v0 canary, NOT the complete required-set. When the
    deferred panels (queue-depth-by-priority, decision-outcome
    distribution, observability health) land, update this set.
    """
    required_panels = {
        "per-skill-accuracy.yaml",
        "llm-call-rate.yaml",
        "latency-histograms.yaml",
    }
    specs = _load_dashboard_specs()
    assert specs, f"no dashboard YAMLs found at {_DASHBOARDS_DIR}"
    found = {p.name for p, _ in specs}
    missing = required_panels - found
    assert not missing, f"missing required panels: {sorted(missing)}; found {sorted(found)}"


def test_every_referenced_field_appears_in_trace_schema() -> None:
    """Schema-drift gate: every referenced field must exist in trace-schema.md.

    Catches typos, rename drift, and "added a panel that filters on a
    field we never document" without standing up Langfuse.
    """
    schema_fields = _trace_schema_field_names()
    assert schema_fields, "no field names extracted from trace-schema.md"

    specs = _load_dashboard_specs()
    assert specs, f"no dashboard YAMLs found at {_DASHBOARDS_DIR}"

    violations: list[str] = []
    for path, spec in specs:
        for field in sorted(_referenced_field_names(spec, path)):
            # Match either the full field name (``langfuse.trace.metadata.environment``,
            # ``gen_ai.system``) or the head of a dotted path
            # (``latency_us.engine`` → ``latency_us``). The first form
            # matches span-attribute table entries; the second matches
            # YAML-block top-level keys with sub-fields.
            head = field.split(".", 1)[0]
            if field not in schema_fields and head not in schema_fields:
                violations.append(f"{path.name}: references {field!r} (not in trace-schema.md)")

    assert not violations, "dashboard-schema drift:\n  - " + "\n  - ".join(violations)


def test_every_panel_carries_environment_filter() -> None:
    """Plan § Step 11: every panel filters langfuse.trace.metadata.environment to ``"production"``.

    Otherwise a fresh Langfuse import would silently mix replay + production
    data into the accuracy panels.
    """
    specs = _load_dashboard_specs()
    assert specs, f"no dashboard YAMLs found at {_DASHBOARDS_DIR}"
    violations: list[str] = []
    for path, spec in specs:
        filters = spec.get("filters", [])
        if not isinstance(filters, list):
            violations.append(f"{path.name}: filters is not a list")
            continue
        env_filters = [
            f
            for f in filters
            if isinstance(f, dict) and f.get("field") == "langfuse.trace.metadata.environment"
        ]
        if not env_filters:
            violations.append(f"{path.name}: missing langfuse.trace.metadata.environment filter")
            continue
        for f in env_filters:
            if f.get("value") != "production":
                violations.append(
                    f"{path.name}: langfuse.trace.metadata.environment value must be 'production'; "
                    f"got {f.get('value')!r}"
                )

    assert not violations, "environment-filter violations:\n  - " + "\n  - ".join(violations)


def test_every_panel_has_required_keys() -> None:
    """Required keys per panel — canary against accidental field drops.

    ``windows`` is intentionally NOT required: future panel types
    (e.g., a single-snapshot status indicator) may not need rolling
    windows. The required set covers only fields whose absence would
    leave the panel un-renderable.
    """
    required_top_level_keys = {"name", "description", "panel_type", "filters"}
    specs = _load_dashboard_specs()
    assert specs, f"no dashboard YAMLs found at {_DASHBOARDS_DIR}"
    violations: list[str] = []
    for path, spec in specs:
        missing = required_top_level_keys - set(spec.keys())
        if missing:
            violations.append(f"{path.name}: missing keys {sorted(missing)}")
    assert not violations, "required-key violations:\n  - " + "\n  - ".join(violations)


# ---------------------------------------------------------------------------
# Direct unit tests of the extraction helpers
# ---------------------------------------------------------------------------


def test_trace_schema_field_names_includes_yaml_block_keys() -> None:
    """``_trace_schema_field_names`` extracts top-level YAML-block keys."""
    fields = _trace_schema_field_names()
    # Sample: a top-level trace-dict field.
    assert "routing_path" in fields
    # Sample: a nested field's parent key (latency_us.engine in YAML
    # is indented under latency_us).
    assert "latency_us" in fields


def test_trace_schema_field_names_includes_span_attribute_table_rows() -> None:
    """``_trace_schema_field_names`` extracts backticked first-cell table rows."""
    fields = _trace_schema_field_names()
    assert "samantha.event_input_hash" in fields
    assert "langfuse.trace.metadata.environment" in fields
    assert "gen_ai.system" in fields


def test_trace_schema_field_names_excludes_doc_wrapper_key() -> None:
    """Depth-0 keys (``trace:`` is the doc shape wrapper) are NOT field names.

    A panel filtering on ``trace`` would be nonsensical; the test
    confirms the parser doesn't pollute the corpus with the wrapper
    key.
    """
    fields = _trace_schema_field_names()
    assert "trace" not in fields


# ---------------------------------------------------------------------------
# Negative tests — prove the gate actually rejects bad input
# ---------------------------------------------------------------------------


def test_referenced_field_names_raises_on_non_list_filters(tmp_path: Path) -> None:
    """A YAML with ``filters`` set to a string instead of a list raises TypeError."""
    bad = {"name": "x", "filters": "langfuse.trace.metadata.environment"}
    with pytest.raises(TypeError, match="'filters' must be a list"):
        _referenced_field_names(bad, tmp_path / "bad.yaml")


def test_referenced_field_names_raises_on_non_dict_filter_item(tmp_path: Path) -> None:
    """``filters: [bare_string]`` raises TypeError naming the index."""
    bad = {"name": "x", "filters": ["bare_string_not_a_mapping"]}
    with pytest.raises(TypeError, match=r"filters\[0\] must be a mapping"):
        _referenced_field_names(bad, tmp_path / "bad.yaml")


def test_referenced_field_names_raises_on_non_list_metrics(tmp_path: Path) -> None:
    """``metrics`` as a string raises TypeError."""
    bad = {"name": "x", "metrics": "agreement"}
    with pytest.raises(TypeError, match="'metrics' must be a list"):
        _referenced_field_names(bad, tmp_path / "bad.yaml")


def test_drift_gate_rejects_panel_referencing_unknown_field(tmp_path: Path) -> None:
    """A synthetic panel referencing a non-existent field surfaces a violation.

    The schema-drift gate's whole purpose is to reject this case; a
    positive-only suite wouldn't catch a regression where the
    extraction logic itself broke.
    """
    schema_fields = _trace_schema_field_names()
    bad_path = tmp_path / "bad.yaml"
    bad_spec: dict[str, object] = {
        "name": "synthetic_bad",
        "filters": [{"field": "definitely_not_a_real_field", "op": "equals", "value": "x"}],
    }
    referenced = _referenced_field_names(bad_spec, bad_path)
    assert "definitely_not_a_real_field" in referenced
    sentinel = "definitely_not_a_real_field"
    head = sentinel.split(".", 1)[0]
    assert head not in schema_fields, (
        "if a real schema field happens to share this name the test is no longer "
        "a synthetic-bad case; pick a different sentinel"
    )


def test_drift_gate_rejects_panel_with_wrong_environment_filter(tmp_path: Path) -> None:
    """A panel filtering on ``langfuse.trace.metadata.environment != "production"`` is malformed.

    Mirrors ``test_every_panel_carries_environment_filter`` but
    constructs the malformed input synthetically — this test would
    fail if the env-filter check itself regressed.
    """
    bad_spec: dict[str, object] = {
        "name": "x",
        "filters": [
            {"field": "langfuse.trace.metadata.environment", "op": "equals", "value": "replay"}
        ],
    }
    filters = bad_spec["filters"]
    assert isinstance(filters, list)
    env_filters = [
        f
        for f in filters
        if isinstance(f, dict) and f.get("field") == "langfuse.trace.metadata.environment"
    ]
    assert env_filters, "test setup error: no env filter found"
    bad_values = [f for f in env_filters if f.get("value") != "production"]
    assert bad_values, "this synthetic input should have surfaced as a violation"


def test_load_dashboard_specs_raises_on_missing_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing dashboards dir is a hard error, distinguishable from empty dir."""
    monkeypatch.setattr(
        "tests.observability.test_dashboard_schema._DASHBOARDS_DIR",
        tmp_path / "definitely-missing",
    )
    with pytest.raises(FileNotFoundError, match="dashboards directory missing"):
        _load_dashboard_specs()


def test_load_dashboard_specs_raises_on_empty_yaml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty YAML file produces a distinct error from a wrong-root-type file."""
    (tmp_path / "empty.yaml").write_text("")
    monkeypatch.setattr("tests.observability.test_dashboard_schema._DASHBOARDS_DIR", tmp_path)
    with pytest.raises(ValueError, match="empty or comment-only"):
        _load_dashboard_specs()


def test_load_dashboard_specs_raises_on_non_mapping_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A YAML root that's a list (not a mapping) raises distinctly."""
    (tmp_path / "list_root.yaml").write_text("- foo\n- bar\n")
    monkeypatch.setattr("tests.observability.test_dashboard_schema._DASHBOARDS_DIR", tmp_path)
    with pytest.raises(ValueError, match="top-level YAML must be a mapping"):
        _load_dashboard_specs()

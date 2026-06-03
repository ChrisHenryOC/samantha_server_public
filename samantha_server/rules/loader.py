"""Loader for YAML rule specs — predicate builder and rule file ingestion.

No LLM imports. Deterministic path only.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, get_args

import yaml
from pydantic import ValidationError

from samantha_server.primitives import (
    PRIMITIVE_REGISTRY,
    BooleanAnd,
    BooleanOr,
    Not,
    Primitive,
)

if TYPE_CHECKING:
    from samantha_server.rules.spec import RuleSpec


def _build_severity_order() -> dict[str, int]:
    from samantha_server.rules.spec import Severity

    return {sev: i for i, sev in enumerate(get_args(Severity))}


_SEVERITY_ORDER: dict[str, int] = _build_severity_order()


def build_predicate(node: Mapping[str, Any]) -> Primitive:
    """Recursively convert a one-key YAML mapping to the corresponding Primitive.

    Each call expects exactly one key naming the primitive (e.g. ``is_null``,
    ``boolean_and``).  Dispatches to the PRIMITIVE_REGISTRY for atomics and
    handles combinator construction recursively.

    Raises ValueError for unknown primitive names, including ``rule_ref`` which
    must be resolved by load_rule_specs before predicate construction.
    """
    if len(node) != 1:
        raise ValueError(f"Predicate node must have exactly one key, got: {list(node.keys())}")

    key, payload = next(iter(node.items()))

    if key == "rule_ref":
        raise ValueError(
            "rule_ref must be resolved by load_rule_specs before predicate construction"
        )

    if key not in PRIMITIVE_REGISTRY:
        raise ValueError(
            f"Unknown primitive '{key}'. Valid primitives: {sorted(PRIMITIVE_REGISTRY)}"
        )

    cls = PRIMITIVE_REGISTRY[key]

    if cls is Not:
        if not isinstance(payload, Mapping):
            raise ValueError(
                f"'not' payload must be a mapping (one-key predicate node),"
                f" got {type(payload).__name__}"
            )
        child = build_predicate(payload)
        return Not(child=child)

    if cls in (BooleanAnd, BooleanOr):
        combinator_name = key
        if not isinstance(payload, list):
            raise ValueError(
                f"'{combinator_name}' payload must be a list of predicate nodes, "
                f"got {type(payload).__name__}"
            )
        if len(payload) < 2:
            raise ValueError(
                f"'{combinator_name}' requires at least 2 children, got {len(payload)}"
            )
        children = tuple(build_predicate(child_node) for child_node in payload)
        return cls(children=children)

    return cls(**payload)


def _substitute_rule_refs(
    node: Any,
    raw_by_id: dict[str, dict[str, Any]],
    source_id: str,
    source_file: str,
    visiting: tuple[str, ...],
) -> Any:
    """Recursively walk a when-predicate subtree and substitute rule_ref nodes.

    ``node`` may be a dict (predicate node), a list (combinator children), or a
    scalar.  Returns a new structure — never mutates the input.

    Raises ValueError on unknown rule_id, non-string rule_ref payload, or cycles.
    """
    if isinstance(node, list):
        return [
            _substitute_rule_refs(item, raw_by_id, source_id, source_file, visiting)
            for item in node
        ]

    if not isinstance(node, dict):
        return node

    if len(node) == 1 and "rule_ref" in node:
        ref_id = node["rule_ref"]
        if not isinstance(ref_id, str):
            raise ValueError(
                f"{source_file}: rule_ref requires a string rule_id,"
                f" got {type(ref_id).__name__!r} in rule '{source_id}'"
            )
        if ref_id not in raw_by_id:
            raise ValueError(
                f"{source_file}: rule '{source_id}' references unknown rule_id '{ref_id}'"
            )
        if ref_id in visiting:
            cycle_path = " -> ".join((*visiting, ref_id))
            raise ValueError(
                f"{source_file}: cycle detected resolving rule_ref in '{source_id}';"
                f" rule_ids involved: {cycle_path}"
            )
        return _substitute_rule_refs(
            raw_by_id[ref_id]["when"], raw_by_id, ref_id, source_file, (*visiting, ref_id)
        )

    return {
        k: _substitute_rule_refs(v, raw_by_id, source_id, source_file, visiting)
        for k, v in node.items()
    }


def _resolve_rule_refs(
    raw_by_id: dict[str, dict[str, Any]],
    path_by_id: dict[str, Path],
) -> dict[str, dict[str, Any]]:
    """Pass 2: replace all rule_ref nodes in every spec's when subtree.

    Returns a new dict of fully-resolved raw spec mappings.  The originals
    are not mutated.
    """
    resolved: dict[str, dict[str, Any]] = {}
    for rule_id, raw in raw_by_id.items():
        source_file = path_by_id[rule_id].name
        resolved_spec = dict(raw)
        resolved_spec["when"] = _substitute_rule_refs(
            raw["when"],
            raw_by_id,
            rule_id,
            source_file,
            (rule_id,),
        )
        resolved[rule_id] = resolved_spec
    return resolved


def load_rule_spec_from_dict(data: Mapping[str, Any]) -> RuleSpec:
    """Validate and construct a RuleSpec from a raw mapping (e.g. from yaml.safe_load)."""
    from samantha_server.rules.spec import RuleSpec

    return RuleSpec(**data)


_YAML_SUFFIXES: frozenset[str] = frozenset({".yaml", ".yml"})


def _validate_required_keys(raw: dict[str, Any], path: Path) -> None:
    """Raise ValueError with filename attribution when required top-level keys are missing.

    Checks that ``rule_id`` is present and non-empty, and that ``when`` is present.
    Place this check before duplicate detection so the first offending file fails loudly.
    """
    rule_id = raw.get("rule_id", "")
    if not isinstance(rule_id, str) or not rule_id.strip():
        raise ValueError(f"{path.name}: missing or empty 'rule_id'")
    if "when" not in raw:
        raise ValueError(f"{path.name}: missing required key 'when'")


def _load_raw_specs_from_dir(
    directory: Path,
    *,
    allow_missing: bool,
    raise_on_duplicate: bool,
) -> tuple[dict[str, dict[str, Any]], dict[str, Path], list[str]]:
    """Pass-1 YAML scan: parse every .yaml/.yml file in *directory* into raw dicts.

    Returns (raw_by_id, path_by_id, ordered_ids).

    When *allow_missing* is True and *directory* does not exist or is not a
    directory, returns empty structures instead of raising.  When False, raises
    ValueError if the directory is absent or not a directory.

    When *raise_on_duplicate* is True, a duplicate rule_id across two files
    raises ValueError.  When False, the later file silently overwrites the
    earlier one (used when the caller will add its own entry afterward).

    Non-dict YAML roots: always raises ValueError (so invalid files surface
    loudly) unless the root is non-dict and *allow_missing* is True, in which
    case non-dict roots are silently skipped (load_candidate_rule_spec only
    cares about specs that resolve correctly).
    """
    if not directory.exists() or not directory.is_dir():
        if allow_missing:
            return {}, {}, []
        raise ValueError(
            f"Expected a directory at '{directory}', but it does not exist or is not a directory"
        )

    raw_by_id: dict[str, dict[str, Any]] = {}
    path_by_id: dict[str, Path] = {}
    ordered_ids: list[str] = []

    for path in sorted(directory.iterdir()):
        if path.suffix not in _YAML_SUFFIXES:
            continue
        try:
            raw = yaml.safe_load(path.read_text())
        except yaml.YAMLError as exc:
            raise ValueError(f"{path.name}: YAML parse error: {exc}") from exc

        if not isinstance(raw, dict):
            if allow_missing:
                continue
            raise ValueError(
                f"{path.name}: expected a YAML mapping at document root, got {type(raw).__name__}"
            )

        _validate_required_keys(raw, path)

        rule_id: str = raw["rule_id"]
        if rule_id in raw_by_id and raise_on_duplicate:
            raise ValueError(
                f"Duplicate rule_id '{rule_id}' found in {path.name} "
                f"(first seen in {path_by_id[rule_id].name})"
            )
        raw_by_id[rule_id] = raw
        path_by_id[rule_id] = path
        if rule_id not in ordered_ids:
            ordered_ids.append(rule_id)

    return raw_by_id, path_by_id, ordered_ids


def load_rule_specs(directory: Path) -> list[RuleSpec]:
    """Load all .yaml/.yml rule specs from *directory*.

    Two-pass loading:
      Pass 1 — parse every YAML file into a raw dict, check for duplicates.
      Pass 2 — resolve rule_ref nodes by inlining the referenced when-predicate.
    Then build RuleSpec objects from the resolved dicts.

    Non-recursive: only files directly inside *directory* are loaded;
    subdirectories are not descended into.

    Raises ValueError on parse errors, Pydantic validation failures, duplicate
    rule_id values, unknown rule_ref targets, non-string rule_ref payloads, or
    cycles.  The offending filename is included in every error message.  Also
    raises ValueError when *directory* does not exist or is not a directory.
    """
    from samantha_server.rules.spec import RuleSpec as _RuleSpec

    raw_by_id, path_by_id, ordered_ids = _load_raw_specs_from_dir(
        directory, allow_missing=False, raise_on_duplicate=True
    )

    if not raw_by_id:
        return []

    # Pass 2: resolve rule_ref nodes.
    resolved_by_id = _resolve_rule_refs(raw_by_id, path_by_id)

    # Build RuleSpec objects from resolved dicts.
    specs: list[_RuleSpec] = []
    for rule_id in ordered_ids:
        path = path_by_id[rule_id]
        try:
            spec = load_rule_spec_from_dict(resolved_by_id[rule_id])
        except (ValidationError, ValueError, TypeError) as exc:
            raise ValueError(f"{path.name}: {exc}") from exc
        specs.append(spec)

    return specs


def load_candidate_rule_spec(candidate_path: Path, specs_dir: Path) -> RuleSpec:
    """Load a candidate rule YAML, resolving any rule_refs against existing specs in *specs_dir*.

    Parses the real specs directory into a raw_by_id map, adds the candidate,
    runs rule_ref resolution, and returns a RuleSpec built from the resolved candidate.

    Raises ValueError on YAML parse errors, unknown rule_refs, cycles, or validation
    failures.  Raises ValueError with a descriptive message if the candidate's rule_id
    collides with an existing rule_id in *specs_dir* — use a temporary rule_id when
    pre-flighting a modified existing rule.
    """

    # Load the candidate YAML.
    try:
        candidate_raw = yaml.safe_load(candidate_path.read_text())
    except yaml.YAMLError as exc:
        raise ValueError(f"{candidate_path.name}: YAML parse error: {exc}") from exc

    if not isinstance(candidate_raw, dict):
        raise ValueError(
            f"{candidate_path.name}: expected a YAML mapping at document root,"
            f" got {type(candidate_raw).__name__}"
        )
    _validate_required_keys(candidate_raw, candidate_path)
    candidate_id: str = candidate_raw["rule_id"]

    # Pass 1: build raw_by_id from the existing specs directory.
    raw_by_id, path_by_id, _ = _load_raw_specs_from_dir(
        specs_dir, allow_missing=True, raise_on_duplicate=False
    )

    # Guard: reject a candidate whose rule_id already exists in specs_dir.
    if candidate_id in raw_by_id:
        existing_name = path_by_id[candidate_id].name
        raise ValueError(
            f"{candidate_path.name}: rule_id '{candidate_id}' already exists in"
            f" {specs_dir} ({existing_name}). Use a temporary rule_id when"
            " pre-flighting a modified existing rule."
        )

    # Add the candidate into the pool so rule_refs can chain through it if needed.
    raw_by_id[candidate_id] = candidate_raw
    path_by_id[candidate_id] = candidate_path

    # Pass 2: resolve rule_refs across the full pool.
    resolved_by_id = _resolve_rule_refs(raw_by_id, path_by_id)

    # Build and return only the candidate's RuleSpec.
    try:
        spec = load_rule_spec_from_dict(resolved_by_id[candidate_id])
    except (ValidationError, ValueError, TypeError) as exc:
        raise ValueError(f"{candidate_path.name}: {exc}") from exc
    return spec


class RuleIndex:
    """Indexes a list of RuleSpec objects for efficient dispatch."""

    rules_by_step: dict[str, list[RuleSpec]]
    rules_by_applies_at: dict[str, list[RuleSpec]]
    all_rules: list[RuleSpec]
    _rule_id_set: frozenset[str]

    def __init__(self, specs: list[RuleSpec]) -> None:
        from samantha_server.rules.spec import RuleSpec as _RuleSpec

        seen_ids: set[str] = set()
        by_step: dict[str, list[_RuleSpec]] = {}
        by_applies_at: dict[str, list[_RuleSpec]] = {}

        for i, spec in enumerate(specs):
            if spec.rule_id in seen_ids:
                raise ValueError(f"Duplicate rule_id '{spec.rule_id}' at position {i} in spec list")
            seen_ids.add(spec.rule_id)

            by_step.setdefault(spec.step, []).append(spec)

            if spec.applies_at is not None:
                assert spec.step == "IHC", (
                    f"Rule '{spec.rule_id}' has applies_at='{spec.applies_at}'"
                    f" but step='{spec.step}' — only IHC rules may have applies_at"
                )
                by_applies_at.setdefault(spec.applies_at, []).append(spec)

        def _sev_key(r: _RuleSpec) -> int:
            return _SEVERITY_ORDER[r.severity]  # type: ignore[index]

        def _pri_key(r: _RuleSpec) -> int:
            assert r.priority is not None
            return r.priority

        def _check_unique_priorities(rules: list[_RuleSpec], bucket_label: str) -> None:
            """Raise if two rules in the same dispatch bucket share a priority.

            Without this guard, the dispatcher's stable sort would tie-break by
            insertion order (filesystem-sort-dependent for YAML loads), producing
            silent, host-dependent dispatch decisions.
            """
            seen: dict[int, str] = {}
            for r in rules:
                if r.priority is None:
                    continue
                if r.priority in seen:
                    raise ValueError(
                        f"Duplicate priority {r.priority} in {bucket_label}: "
                        f"rules '{seen[r.priority]}' and '{r.rule_id}' both have "
                        f"priority {r.priority}. Within-bucket dispatch order would "
                        f"depend on filesystem/insertion ordering — pick distinct priorities."
                    )
                seen[r.priority] = r.rule_id

        for step, rules in by_step.items():
            if step == "ACCESSIONING":
                # ACC dispatches by severity; priority is null per Pydantic invariant
                # (_check_step_invariants in spec.py rejects priority on ACC rules).
                # The duplicate-priority guard would never fire here, but is also
                # not needed — pinned by test_accessioning_with_null_priorities_does_not_raise.
                rules.sort(key=_sev_key)
            elif step == "IHC":
                # IHC dispatches via by_applies_at, never via by_step. Cross-applies_at
                # priority sharing is intentional (e.g., IHC-001 and IHC-006 can both
                # have priority 1 in different applies_at sub-buckets), so applying the
                # uniqueness guard here would false-positive. Drop the bucket entirely
                # (below) so future tooling iterating rules_by_step can't silently
                # observe the cross-applies_at-merged list.
                pass
            else:
                rules.sort(key=_pri_key)
                _check_unique_priorities(rules, f"step '{step}'")

        for applies_at, rules in by_applies_at.items():
            rules.sort(key=_pri_key)
            _check_unique_priorities(rules, f"applies_at '{applies_at}'")

        # Drop the IHC bucket from rules_by_step — see comment above.
        # IHC rules remain accessible via rules_by_applies_at and all_rules.
        by_step.pop("IHC", None)

        self.all_rules = specs
        self.rules_by_step = by_step
        self.rules_by_applies_at = by_applies_at
        # PR205 review #3: retain the duplicate-detection set as a frozenset so
        # __contains__ is O(1). all_rules linear scan grows with the corpus and
        # is called per non-error step by the GH-194 hallucination gate (~2000+
        # calls per replay run today).
        self._rule_id_set = frozenset(seen_ids)

    def __contains__(self, rule_id: object) -> bool:
        """Return True iff *rule_id* is a known rule in this index. O(1)."""
        return rule_id in self._rule_id_set

"""Rule corpus sweep — verify string literals against canonical fields are casefolded.

Every string literal in a rule predicate that compares against a canonicalizable field
(specimen_type, anatomic_site, fixative, ordered_tests, priority) must already be in
casefolded (lowercase + trimmed) form.

This ensures that the canonical form in rule specs is unambiguous: the spec author
always writes the canonical form, and canonicalization only needs to normalize the
data-side strings (Order fields from external messages).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from samantha_server.canonicalization import CANONICAL_FIELDS

_SPECS_DIR = Path(__file__).resolve().parents[2] / "samantha_server" / "rules" / "specs"


def _collect_string_values_for_field(spec: dict[str, Any], target_field: str) -> list[str]:
    """Walk a rule spec dict and collect all string literals that appear
    as 'value' or inside 'values' lists alongside a matching 'field' key."""
    results: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            field_name = node.get("field")
            if field_name == target_field:
                # Scalar value
                if "value" in node and isinstance(node["value"], str):
                    results.append(node["value"])
                # List of values (in_enum)
                if "values" in node and isinstance(node["values"], list):
                    for v in node["values"]:
                        if isinstance(v, str):
                            results.append(v)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(spec)
    return results


class TestCanonicalRuleLiterals:
    """Every string literal in a rule spec predicate against a canonicalizable
    field must be in casefolded (lowercase + trimmed) form."""

    def _load_specs(self) -> list[tuple[str, dict[str, Any]]]:
        specs = []
        for yaml_file in sorted(_SPECS_DIR.glob("*.yaml")):
            with yaml_file.open(encoding="utf-8") as fh:
                spec: dict[str, Any] = yaml.safe_load(fh)
            specs.append((yaml_file.stem, spec))
        return specs

    def test_all_canonicalizable_field_literals_are_casefolded(self) -> None:
        """Every string literal against a canonical field must be casefolded."""
        violations: list[str] = []
        for rule_id, spec in self._load_specs():
            for field in CANONICAL_FIELDS:
                literals = _collect_string_values_for_field(spec, field)
                for literal in literals:
                    if literal != literal.casefold():
                        violations.append(
                            f"{rule_id}: field={field!r} literal={literal!r} "
                            f"(expected casefolded={literal.casefold()!r})"
                        )
        assert not violations, (
            "Rule literals not in casefolded form (canonical-literals sweep required):\n"
            + "\n".join(violations)
        )

    def test_all_canonicalizable_field_literals_are_stripped(self) -> None:
        """Every string literal against a canonical field must be stripped of whitespace."""
        violations: list[str] = []
        for rule_id, spec in self._load_specs():
            for field in CANONICAL_FIELDS:
                literals = _collect_string_values_for_field(spec, field)
                for literal in literals:
                    if literal != literal.strip():
                        violations.append(
                            f"{rule_id}: field={field!r} literal={literal!r} "
                            f"(has leading/trailing whitespace)"
                        )
        assert not violations, "Rule literals with leading/trailing whitespace:\n" + "\n".join(
            violations
        )

    def test_acc004_values_has_no_uppercase_fna(self) -> None:
        """ACC-004: FNA (uppercase) must be removed — only lowercase fna is needed
        now that canonicalization normalizes case at comparison time."""
        acc004_path = _SPECS_DIR / "ACC-004.yaml"
        with acc004_path.open(encoding="utf-8") as fh:
            spec: dict[str, Any] = yaml.safe_load(fh)

        all_values: list[str] = []
        when = spec.get("when", {})
        in_enum = when.get("in_enum", {})
        if isinstance(in_enum, dict):
            all_values = [v for v in in_enum.get("values", []) if isinstance(v, str)]

        assert "FNA" not in all_values, (
            "ACC-004 still contains 'FNA' (uppercase) — remove it; "
            "canonicalization makes it redundant with 'fna'"
        )

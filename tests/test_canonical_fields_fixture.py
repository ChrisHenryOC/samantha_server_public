"""Tests for the canonical-fields.json pick-list (production path).

Verifies the JSON is well-formed and each field's pick list is non-empty.
Reads from samantha_server/data/ — the production source of truth — so there
is no divergence between the installed package and the test fixture.
"""

import importlib.resources
import json
from pathlib import Path
from typing import Any

FIXTURE_PATH = Path(
    str(importlib.resources.files("samantha_server.data") / "canonical-fields.json")
)

CANONICAL_FIELDS = {"specimen_type", "anatomic_site", "fixative", "priority", "ordered_tests"}


class TestCanonicalFieldsFixture:
    def _load(self) -> dict[str, Any]:
        return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))  # type: ignore[no-any-return]

    def test_fixture_file_exists(self) -> None:
        assert FIXTURE_PATH.exists(), f"canonical-fields.json not found at {FIXTURE_PATH}"

    def test_fixture_parses_as_json(self) -> None:
        data = self._load()
        assert isinstance(data, dict)

    def test_all_canonical_fields_present(self) -> None:
        data = self._load()
        for field in CANONICAL_FIELDS:
            assert field in data, f"Field '{field}' missing from canonical-fields.json"

    def test_each_field_pick_list_is_non_empty(self) -> None:
        data = self._load()
        for field in CANONICAL_FIELDS:
            pick_list = data[field]
            assert isinstance(pick_list, list), f"Field '{field}' value must be a list"
            assert len(pick_list) > 0, f"Field '{field}' pick list must be non-empty"

    def test_all_values_are_strings(self) -> None:
        data = self._load()
        for field in CANONICAL_FIELDS:
            for value in data[field]:
                assert isinstance(value, str), (
                    f"Field '{field}' contains non-string value: {value!r}"
                )

    def test_all_values_are_casefolded(self) -> None:
        """Every value in the pick list must already be in casefolded form."""
        data = self._load()
        for field in CANONICAL_FIELDS:
            for value in data[field]:
                assert value == value.casefold(), (
                    f"Field '{field}' value {value!r} is not casefolded "
                    f"(expected {value.casefold()!r})"
                )

    def test_all_values_are_stripped(self) -> None:
        """Every value must have no leading/trailing whitespace."""
        data = self._load()
        for field in CANONICAL_FIELDS:
            for value in data[field]:
                assert value == value.strip(), (
                    f"Field '{field}' value {value!r} has leading/trailing whitespace"
                )

    def test_no_duplicate_values_per_field(self) -> None:
        data = self._load()
        for field in CANONICAL_FIELDS:
            values: list[str] = data[field]
            assert len(values) == len(set(values)), (
                f"Field '{field}' has duplicate values: {values}"
            )

    def test_specimen_type_contains_biopsy(self) -> None:
        data = self._load()
        assert "biopsy" in data["specimen_type"]

    def test_specimen_type_contains_fna(self) -> None:
        data = self._load()
        assert "fna" in data["specimen_type"]

    def test_anatomic_site_contains_breast_variants(self) -> None:
        data = self._load()
        assert "breast" in data["anatomic_site"]
        assert "left breast" in data["anatomic_site"]
        assert "right breast" in data["anatomic_site"]

    def test_fixative_contains_formalin(self) -> None:
        data = self._load()
        assert "formalin" in data["fixative"]

    def test_priority_contains_routine(self) -> None:
        data = self._load()
        assert "routine" in data["priority"]

    def test_ordered_tests_contains_her2(self) -> None:
        data = self._load()
        assert "her2" in data["ordered_tests"]

    def test_ordered_tests_contains_breast_ihc_panel(self) -> None:
        data = self._load()
        assert "breast ihc panel" in data["ordered_tests"]


class TestAnatomicSiteNoDrift:
    """Structural drift-check between canonical-fields.json and rule specs.

    Invariant: every value in canonical-fields.json anatomic_site must appear in
    exactly one of: ACC-003's blacklist, ACC-011's whitelist, or the explicit
    middle band (_KNOWN_MIDDLE_BAND below). The middle band must be non-empty
    (the LLM-review band is reachable). No orphan canonical values; no
    double-listed values.

    PR245 extended coverage (H1 + H3 + M4):
    - H1: `_ACC003_ANATOMIC_SITE_BLACKLIST` and `_ACC011_ANATOMIC_SITE_WHITELIST`
      constants in handlers.py are a third copy of the rule lists. These tests
      assert that those constants stay in sync with the YAML sources. A one-byte
      change in either constant will cause the corresponding test to fail.
    - H3: the middle band is now an explicit named set (_KNOWN_MIDDLE_BAND).
      Adding a value to canonical-fields.json without placing it in a rule list
      OR adding it to _KNOWN_MIDDLE_BAND here causes the invariant test to fail.
    - M4: the canonical/blacklist overlap ("lung" is in both) is now an explicit
      policy assertion. Any future canonical value added to the blacklist must
      also be listed in _INTENTIONAL_CANONICAL_BLACKLIST_OVERLAP, or removed
      from the blacklist.
    """

    _SPECS_DIR = Path(__file__).resolve().parents[1] / "samantha_server" / "rules" / "specs"

    # Values in canonical-fields.json anatomic_site that intentionally sit in
    # neither ACC-003's blacklist nor ACC-011's whitelist. These route to
    # ACC-011 → PENDING_LLM_REVIEW. Extend this set (and update the rule
    # YAMLs or not) whenever a new canonical value is intentionally middle-band.
    _KNOWN_MIDDLE_BAND: frozenset[str] = frozenset({"chest wall", "skin overlying breast"})

    # Canonical values that are also in ACC-003's blacklist. "lung" is a known
    # canonical value that unambiguously routes to DO_NOT_PROCESS, so it lives
    # in both lists by design. Any future canonical value added to the blacklist
    # must be listed here explicitly, making the policy machine-readable.
    _INTENTIONAL_CANONICAL_BLACKLIST_OVERLAP: frozenset[str] = frozenset({"lung"})

    def _load_acc003_blacklist(self) -> frozenset[str]:
        """Load ACC-003's in_enum values (blacklist) from the parsed rule spec."""
        from samantha_server.primitives import InEnum
        from samantha_server.rules.loader import load_rule_specs
        from samantha_server.rules.spec import RuleSpec

        specs: list[RuleSpec] = load_rule_specs(self._SPECS_DIR)
        acc003 = next(s for s in specs if s.rule_id == "ACC-003")
        assert isinstance(acc003.when, InEnum), "ACC-003.when must be InEnum (blacklist)"
        return frozenset(acc003.when.values)

    def _load_acc011_whitelist(self) -> frozenset[str]:
        """Load ACC-011's whitelist (third Not>in_enum child) from the parsed rule spec."""
        from samantha_server.primitives import BooleanAnd, InEnum, Not
        from samantha_server.rules.loader import load_rule_specs
        from samantha_server.rules.spec import RuleSpec

        specs: list[RuleSpec] = load_rule_specs(self._SPECS_DIR)
        acc011 = next(s for s in specs if s.rule_id == "ACC-011")
        assert isinstance(acc011.when, BooleanAnd), "ACC-011.when must be BooleanAnd"
        # children[2]: Not(in_enum(whitelist))
        whitelist_not = acc011.when.children[2]
        assert isinstance(whitelist_not, Not)
        assert isinstance(whitelist_not.child, InEnum)
        return frozenset(whitelist_not.child.values)

    def test_anatomic_site_no_drift(self) -> None:
        """Every canonical anatomic_site value appears in at most one of:
        ACC-003 blacklist or ACC-011 whitelist. No value is double-listed.
        The middle band (in neither list) must be non-empty — the LLM-review
        path must be reachable for at least one canonical value.
        """
        canonical = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))["anatomic_site"]
        canonical_set = frozenset(canonical)

        blacklist = self._load_acc003_blacklist()
        whitelist = self._load_acc011_whitelist()

        # No value may appear in both blacklist and whitelist.
        double_listed = blacklist & whitelist
        assert not double_listed, (
            f"Values appear in both ACC-003 blacklist and ACC-011 whitelist: "
            f"{sorted(double_listed)}. Remove duplicates from one of the rule YAMLs."
        )

        # Whitelist orphans would be a bug (they claim a canonical value bypasses
        # LLM review, but that value isn't in the system). Blacklist orphans are
        # acceptable: the blacklist intentionally includes non-canonical values
        # (liver, colon, brain, prostate) that are common off-vocab rejections.
        whitelist_orphans = whitelist - canonical_set
        assert not whitelist_orphans, (
            f"ACC-011 whitelist contains values not in canonical-fields.json: "
            f"{sorted(whitelist_orphans)}. Remove stale entries from ACC-011.yaml."
        )

        # The middle band (canonical values in neither list) must be non-empty.
        middle_band = canonical_set - blacklist - whitelist
        assert middle_band, (
            "Middle band (LLM-review band) is empty: every canonical anatomic_site "
            "value is in either the blacklist or whitelist. At least one canonical "
            "value must route through ACC-011 → LLM review."
        )

    # ---------------------------------------------------------------------------
    # H1: constants ↔ YAML drift guard
    # ---------------------------------------------------------------------------

    def test_handler_blacklist_constant_matches_acc003_yaml(self) -> None:
        """H1: _ACC003_ANATOMIC_SITE_BLACKLIST in handlers.py must equal the
        in_enum values parsed from ACC-003.yaml.

        Fails if either the constant or the YAML is edited without updating the
        other — surfacing the drift that was previously invisible to the test suite.
        """
        from samantha_server.llm.handlers import _ACC003_ANATOMIC_SITE_BLACKLIST

        yaml_blacklist = self._load_acc003_blacklist()
        assert yaml_blacklist == _ACC003_ANATOMIC_SITE_BLACKLIST, (
            f"handlers._ACC003_ANATOMIC_SITE_BLACKLIST diverges from ACC-003.yaml.\n"
            f"  constant only: {sorted(_ACC003_ANATOMIC_SITE_BLACKLIST - yaml_blacklist)}\n"
            f"  YAML only:     {sorted(yaml_blacklist - _ACC003_ANATOMIC_SITE_BLACKLIST)}"
        )

    def test_handler_whitelist_constant_matches_acc011_yaml(self) -> None:
        """H1: _ACC011_ANATOMIC_SITE_WHITELIST in handlers.py must equal the
        third boolean_and clause's in_enum values parsed from ACC-011.yaml.

        Fails if either the constant or the YAML is edited without updating the
        other — surfacing the drift that was previously invisible to the test suite.
        """
        from samantha_server.llm.handlers import _ACC011_ANATOMIC_SITE_WHITELIST

        yaml_whitelist = self._load_acc011_whitelist()
        assert yaml_whitelist == _ACC011_ANATOMIC_SITE_WHITELIST, (
            f"handlers._ACC011_ANATOMIC_SITE_WHITELIST diverges from ACC-011.yaml.\n"
            f"  constant only: {sorted(_ACC011_ANATOMIC_SITE_WHITELIST - yaml_whitelist)}\n"
            f"  YAML only:     {sorted(yaml_whitelist - _ACC011_ANATOMIC_SITE_WHITELIST)}"
        )

    # ---------------------------------------------------------------------------
    # H3: explicit middle-band invariant
    # ---------------------------------------------------------------------------

    def test_canonical_middle_band_equals_known_set(self) -> None:
        """H3: canonical_set - (blacklist ∪ whitelist) must equal _KNOWN_MIDDLE_BAND.

        Adding a new value to canonical-fields.json without either:
          (a) placing it in ACC-003's blacklist or ACC-011's whitelist, OR
          (b) adding it to _KNOWN_MIDDLE_BAND in this file,
        causes this test to fail. This forces an explicit routing decision for
        every canonical anatomic_site value.
        """
        canonical = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))["anatomic_site"]
        canonical_set = frozenset(canonical)

        blacklist = self._load_acc003_blacklist()
        whitelist = self._load_acc011_whitelist()

        actual_middle_band = canonical_set - blacklist - whitelist
        assert actual_middle_band == self._KNOWN_MIDDLE_BAND, (
            f"Middle band mismatch.\n"
            f"  actual:   {sorted(actual_middle_band)}\n"
            f"  expected: {sorted(self._KNOWN_MIDDLE_BAND)}\n"
            "Either add the new value to a rule list (ACC-003.yaml blacklist or "
            "ACC-011.yaml whitelist) OR add it explicitly to _KNOWN_MIDDLE_BAND."
        )

    # ---------------------------------------------------------------------------
    # M4: canonical/blacklist overlap policy assertion
    # ---------------------------------------------------------------------------

    def test_canonical_blacklist_overlap_is_intentional(self) -> None:
        """M4: canonical values that also appear in ACC-003's blacklist must be
        listed in _INTENTIONAL_CANONICAL_BLACKLIST_OVERLAP.

        Currently 'lung' is the only such value: it is a canonical anatomic_site
        that unambiguously routes to DO_NOT_PROCESS. Adding a new canonical value
        to the blacklist without updating this set causes this test to fail,
        making the policy machine-readable.
        """
        canonical = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))["anatomic_site"]
        canonical_set = frozenset(canonical)

        blacklist = self._load_acc003_blacklist()

        actual_overlap = canonical_set & blacklist
        assert actual_overlap == self._INTENTIONAL_CANONICAL_BLACKLIST_OVERLAP, (
            f"Canonical/blacklist overlap changed.\n"
            f"  actual:   {sorted(actual_overlap)}\n"
            f"  expected: {sorted(self._INTENTIONAL_CANONICAL_BLACKLIST_OVERLAP)}\n"
            "If a new canonical value was intentionally added to the blacklist, "
            "update _INTENTIONAL_CANONICAL_BLACKLIST_OVERLAP. If the overlap is "
            "unintentional, remove the value from either list."
        )

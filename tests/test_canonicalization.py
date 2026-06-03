"""Tests for samantha_server.canonicalization module."""

import json
from pathlib import Path

import pytest

from samantha_server.canonicalization import CanonicalizationResult, _load_pick_lists, canonicalize


class TestCanonicalizationResult:
    def test_is_namedtuple_with_canonical_and_was_unknown(self) -> None:
        result = CanonicalizationResult(canonical="formalin", was_unknown=False)
        assert result.canonical == "formalin"
        assert result.was_unknown is False

    def test_was_unknown_true(self) -> None:
        result = CanonicalizationResult(canonical="xyz", was_unknown=True)
        assert result.was_unknown is True


class TestCanonicalize:
    # -------------------------------------------------------------------
    # Field not in CANONICAL_FIELDS — casefold but was_unknown=False
    # -------------------------------------------------------------------

    def test_non_canonical_field_returns_folded_with_unknown_false(self) -> None:
        result = canonicalize("patient_name", "Jane Doe")
        assert result.canonical == "jane doe"
        assert result.was_unknown is False

    def test_non_canonical_field_strips_whitespace(self) -> None:
        result = canonicalize("patient_name", "  Jane Doe  ")
        assert result.canonical == "jane doe"
        assert result.was_unknown is False

    # -------------------------------------------------------------------
    # Known field, value in pick list — returns canonical + was_unknown=False
    # -------------------------------------------------------------------

    def test_known_field_known_value_returns_canonical_unknown_false(self) -> None:
        result = canonicalize("fixative", "formalin")
        assert result.canonical == "formalin"
        assert result.was_unknown is False

    def test_known_field_uppercase_value_matches_pick_list(self) -> None:
        result = canonicalize("fixative", "FORMALIN")
        assert result.canonical == "formalin"
        assert result.was_unknown is False

    def test_known_field_mixed_case_value_matches_pick_list(self) -> None:
        result = canonicalize("specimen_type", "Biopsy")
        assert result.canonical == "biopsy"
        assert result.was_unknown is False

    def test_known_field_with_leading_trailing_whitespace_matches(self) -> None:
        result = canonicalize("fixative", "  formalin  ")
        assert result.canonical == "formalin"
        assert result.was_unknown is False

    # -------------------------------------------------------------------
    # Known field, value NOT in pick list — was_unknown=True
    # -------------------------------------------------------------------

    def test_known_field_unknown_value_returns_was_unknown_true(self) -> None:
        result = canonicalize("fixative", "acetone")
        assert result.canonical == "acetone"
        assert result.was_unknown is True

    def test_known_field_unicode_value_casefolded_but_unknown(self) -> None:
        # Deliberately a non-clinical Unicode string — should pass through
        # casefold but not match the pick list, surfacing as was_unknown=True.
        result = canonicalize("specimen_type", "Straße")  # StraBe -> strasse
        assert result.canonical == "strasse"
        assert result.was_unknown is True

    # -------------------------------------------------------------------
    # Edge cases
    # -------------------------------------------------------------------

    def test_empty_string_on_canonical_field_returns_was_unknown_true(self) -> None:
        result = canonicalize("fixative", "")
        assert result.canonical == ""
        assert result.was_unknown is True

    def test_whitespace_only_on_canonical_field_returns_was_unknown_true(self) -> None:
        result = canonicalize("fixative", "   ")
        assert result.canonical == ""
        assert result.was_unknown is True

    def test_empty_string_on_non_canonical_field_returns_was_unknown_false(self) -> None:
        result = canonicalize("patient_name", "")
        assert result.canonical == ""
        assert result.was_unknown is False

    # -------------------------------------------------------------------
    # Every canonical field is recognized
    # -------------------------------------------------------------------

    def test_specimen_type_field_is_recognized(self) -> None:
        result = canonicalize("specimen_type", "biopsy")
        assert result.was_unknown is False

    def test_anatomic_site_field_is_recognized(self) -> None:
        result = canonicalize("anatomic_site", "breast")
        assert result.was_unknown is False

    def test_priority_field_is_recognized(self) -> None:
        result = canonicalize("priority", "routine")
        assert result.was_unknown is False

    def test_ordered_tests_field_is_recognized(self) -> None:
        result = canonicalize("ordered_tests", "her2")
        assert result.was_unknown is False

    # -------------------------------------------------------------------
    # No-import purity guard (deterministic path must not import LLM)
    # -------------------------------------------------------------------

    def test_module_does_not_import_llm_client(self) -> None:
        """Verify canonicalization.py does not contain any LLM imports at the source level."""
        import ast
        from pathlib import Path

        source = (
            Path(__file__).parent.parent / "samantha_server" / "canonicalization.py"
        ).read_text(encoding="utf-8")
        tree = ast.parse(source)

        # Walk all import statements and ensure none reference the LLM package
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "llm" not in alias.name.lower(), (
                        f"canonicalization.py imports LLM module: {alias.name!r}"
                    )
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert "llm" not in module.lower(), (
                    f"canonicalization.py imports from LLM module: {module!r}"
                )


# ---------------------------------------------------------------------------
# _load_pick_lists validation (C-01 + H-03)
# ---------------------------------------------------------------------------


class TestLoadPickLists:
    """_load_pick_lists must validate entry shapes and raise ValueError on malformed input."""

    def _write_json(self, tmp_dir: Path, data: object) -> Path:
        path = tmp_dir / "canonical-fields.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_valid_file_loads_successfully(self, tmp_path: Path) -> None:
        """Well-formed JSON with string lists loads without error."""
        data = {"specimen_type": ["biopsy", "fna"], "_comment": "metadata"}
        path = self._write_json(tmp_path, data)
        fields, pick_lists = _load_pick_lists(path)
        assert "specimen_type" in fields
        assert pick_lists["specimen_type"] == frozenset({"biopsy", "fna"})

    def test_metadata_key_skipped(self, tmp_path: Path) -> None:
        """Keys starting with '_' are skipped and not included in the field set."""
        data = {"_comment": "meta", "fixative": ["formalin"]}
        path = self._write_json(tmp_path, data)
        fields, _ = _load_pick_lists(path)
        assert "_comment" not in fields
        assert "fixative" in fields

    def test_non_list_value_raises_value_error(self, tmp_path: Path) -> None:
        """A field whose value is a string (not a list) must raise ValueError."""
        data = {"specimen_type": "biopsy"}  # string, not list
        path = self._write_json(tmp_path, data)
        with pytest.raises(ValueError, match="specimen_type"):
            _load_pick_lists(path)

    def test_non_list_dict_value_raises_value_error(self, tmp_path: Path) -> None:
        """A field whose value is a dict (not a list) must raise ValueError."""
        data = {"specimen_type": {"biopsy": True}}
        path = self._write_json(tmp_path, data)
        with pytest.raises(ValueError, match="specimen_type"):
            _load_pick_lists(path)

    def test_mixed_type_list_raises_value_error(self, tmp_path: Path) -> None:
        """A list containing a non-string element (int) must raise ValueError."""
        data = {"specimen_type": ["fna", 42, "biopsy"]}
        path = self._write_json(tmp_path, data)
        with pytest.raises(ValueError, match="specimen_type"):
            _load_pick_lists(path)

    def test_null_in_list_raises_value_error(self, tmp_path: Path) -> None:
        """A list containing null must raise ValueError."""
        data = {"specimen_type": ["biopsy", None]}
        path = self._write_json(tmp_path, data)
        with pytest.raises(ValueError, match="specimen_type"):
            _load_pick_lists(path)

    def test_nested_list_in_list_raises_value_error(self, tmp_path: Path) -> None:
        """A list containing a nested list element must raise ValueError."""
        data = {"specimen_type": ["biopsy", ["nested"]]}
        path = self._write_json(tmp_path, data)
        with pytest.raises(ValueError, match="specimen_type"):
            _load_pick_lists(path)

    def test_missing_file_raises_file_not_found(self, tmp_path: Path) -> None:
        """A path that does not exist must raise FileNotFoundError."""
        missing = tmp_path / "does_not_exist.json"
        with pytest.raises(FileNotFoundError):
            _load_pick_lists(missing)

    def test_custom_path_positive(self, tmp_path: Path) -> None:
        """Custom path argument succeeds when the file is well-formed."""
        data = {"fixative": ["formalin", "alcohol"]}
        path = self._write_json(tmp_path, data)
        fields, pick_lists = _load_pick_lists(path)
        assert "fixative" in fields
        assert "formalin" in pick_lists["fixative"]

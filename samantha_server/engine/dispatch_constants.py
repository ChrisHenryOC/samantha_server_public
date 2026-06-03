"""Dispatch constants ported verbatim from samantha-public state_machine.py.

These constants are the canonical source of truth for dispatch ordering in
samantha_server. They are module-level frozen mappings so dispatch is auditable.
"""

from types import MappingProxyType

# Severity hierarchy for accessioning rules (highest severity first).
# Index 0 = highest priority (REJECT wins all others).
#
# Tie-break within a severity tier (GH-231 F-16):
# For ACCESSIONING ties (two rules of equal severity, e.g., two REJECTs in SC-082),
# the winner is filesystem-stable (alphabetic by rule_id). The mechanism: rules are
# loaded via `sorted(directory.iterdir())` in `load_rule_specs` (loader.py:220), and
# filenames match rule IDs — Python's stable sort then preserves alphabetic order
# within a tier. The duplicate-priority guard at loader.py:_check_unique_priorities
# is skipped for ACCESSIONING, so this is intentional behavior. The replay gate
# (post GH-231) compares applied_rules[0] against the engine's applied_rule_id
# (winner), so a tied alphabetically-earlier rule added later would surface as
# mismatch_rules in the affected fixtures.
SEVERITY_ORDER: MappingProxyType[str, int] = MappingProxyType(
    {
        "REJECT": 0,
        "HOLD": 1,
        "PROCEED": 2,
        "ACCEPT": 3,
    }
)

# Mapping from non-IHC workflow states to their rule-catalog step.
# ACCEPTED and MISSING_INFO_PROCEED map to SAMPLE_PREP so that
# grossing_complete events at those states can fire SP-001.
# IHC states are intentionally absent — dispatch uses applies_at for IHC.
STATE_TO_STEP: MappingProxyType[str, str] = MappingProxyType(
    {
        "ACCESSIONING": "ACCESSIONING",
        "ACCEPTED": "SAMPLE_PREP",
        "MISSING_INFO_PROCEED": "SAMPLE_PREP",
        "SAMPLE_PREP_PROCESSING": "SAMPLE_PREP",
        "SAMPLE_PREP_EMBEDDING": "SAMPLE_PREP",
        "SAMPLE_PREP_SECTIONING": "SAMPLE_PREP",
        "SAMPLE_PREP_QC": "SAMPLE_PREP",
        "HE_QC": "HE_QC",
        "PATHOLOGIST_HE_REVIEW": "PATHOLOGIST_HE_REVIEW",
        "RESULTING": "RESULTING",
        "RESULTING_HOLD": "RESULTING",
        "PATHOLOGIST_SIGNOUT": "RESULTING",
        "REPORT_GENERATION": "RESULTING",
    }
)

__all__ = ["SEVERITY_ORDER", "STATE_TO_STEP"]

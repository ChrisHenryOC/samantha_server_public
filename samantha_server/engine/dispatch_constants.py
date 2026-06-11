"""Dispatch constants ported verbatim from samantha-public state_machine.py.

These constants are the canonical source of truth for dispatch ordering in
samantha_server. They are module-level frozen mappings so dispatch is auditable.
"""

from types import MappingProxyType

# Severity hierarchy for accessioning rules (highest severity first).
# Index 0 = highest priority (REJECT wins all others).
#
# Tier ordering: REJECT > HOLD > REVIEW_HOLD > PROCEED > ACCEPT.
# REVIEW_HOLD encodes "specimen held pending LLM review" (ACC-010, ACC-011).
# The maintainer's principle: checks that hold a specimen for processing take
# precedence over checks that flag missing information and proceed (ACC-007).
#
# Flag-union semantics: when the winning rule is REVIEW_HOLD-tier,
# the engine also unions set_flags from matched-but-demoted PROCEED-tier rules
# so ACC-007's MISSING_INFO_PROCEED flag carries alongside ACC-010/011's
# LLM_REVIEW_REQUESTED. Rationale: review resolution goes
# PENDING_LLM_REVIEW -> ACCEPTED directly and never re-runs accessioning,
# so a dropped PROCEED-tier flag is permanently lost to action_handlers and
# transitions flag branching. also_matched records the demoted rule; only
# set_flags are unioned (no transitions/outcomes/clear_flags from demoted rules).
#
# Tie-break within a severity tier:
# For ACCESSIONING ties (two rules of equal severity, e.g., two REJECTs in SC-082),
# the winner is filesystem-stable (alphabetic by rule_id). The mechanism: rules are
# loaded via `sorted(directory.iterdir())` in `load_rule_specs` (loader.py:220), and
# filenames match rule IDs — Python's stable sort then preserves alphabetic order
# within a tier. The duplicate-priority guard at loader.py:_check_unique_priorities
# is skipped for ACCESSIONING, so this is intentional behavior. The replay gate
# (post) compares applied_rules[0] against the engine's applied_rule_id
# (winner), so a tied alphabetically-earlier rule added later would surface as
# mismatch_rules in the affected fixtures.
SEVERITY_ORDER: MappingProxyType[str, int] = MappingProxyType(
    {
        "REJECT": 0,
        "HOLD": 1,
        "REVIEW_HOLD": 2,
        "PROCEED": 3,
        "ACCEPT": 4,
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

# Code Review Issue Matrix Format

## Standard Matrix

| # | Severity | Issue | File:Line | Reviewer(s) | In PR Scope? | Actionable? |
|---|----------|-------|-----------|-------------|--------------|-------------|

## Severity Levels

Severity is about **impact**, not predicted fix priority. Reviewers should not downgrade a finding because "this won't get fixed" — that's the `/fix-review` step's call, not the reviewer's. See `.claude/agents/_base-reviewer.md` §"What is NOT Low" for the calibration anti-pattern guardrails.

- **Critical**: Security vulnerabilities, data loss risks, incorrect workflow state transitions, rule catalog errors
- **High**: >10% performance impact, missing tests for new code, logic errors
- **Medium**: Maintainability, code quality issues
- **Low**: Genuine nits only — cosmetic style, docstring polish, test hygiene without correctness impact

**Lows are not auto-skipped.** `/fix-review`'s EVALUATE LOW-SEVERITY FINDINGS step audits every Low row against explicit upgrade criteria (wrong exception type, latent crash, same-shape-as-existing-finding, missing test for production-only crash path) and re-classifies misclassified Lows as effective-High. Cosmetic Lows that pass the audit are tracked in the FINAL SUMMARY as "Genuinely deferred" with the criterion that excluded them.

## Actionability Criteria

**Actionable = Yes** when ALL true:
- No new dependencies needed
- Changes stay within PR's modified files
- No major refactoring required

**Actionable = No** examples:
- Requires changes to files not in PR
- Would need new library/dependency
- Architectural changes beyond scope

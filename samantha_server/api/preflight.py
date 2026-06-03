"""Stable re-export of preflight from the orchestrator's api package.

The implementation lives in samantha_server.llm.preflight; this module
provides a stable import path (api.preflight) for the orchestrator layer
so callers in api/ don't take a direct dependency on the llm/ package.

Import path stability: once Step 7 deletes the router-shim, callers that
previously imported from llm.preflight will be migrated here. Any new
orchestrator-layer caller should import from api.preflight, not llm.preflight.
"""

from __future__ import annotations

from samantha_server.llm.preflight import PreflightMissing, PreflightOk, PreflightResult, preflight

__all__ = ["PreflightMissing", "PreflightOk", "PreflightResult", "preflight"]

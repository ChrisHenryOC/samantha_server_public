"""samantha_server.tools — developer-diagnostics namespace.

Currently holds `fixture_impact.py`, a replay-impact scanner run during
PR review (pure Python: no LLM imports, no protocol transport, not exposed
to the LLM).

History: an MCP `adapters/` subpackage (`build_*_server` protocol wrappers)
and a `propose_transition.py` Stage A/B grounding wrapper once lived here.
Both were removed as unwired code: no MCP transport was
ever mounted, and the live request router (`samantha_server/api/routing.py`)
calls `engine.evaluate()` directly. The live path uses the engine, scenario,
and skill modules directly.
"""

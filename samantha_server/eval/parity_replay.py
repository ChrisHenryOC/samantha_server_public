"""GH-156: parity-replay CLI.

Replays a samantha POC subset (screening or accumulated_state) through
`samantha_server`'s `replay()` engine and emits a comparison report
shaped against the upstream POC's `results/model_selection_phase1/
summary.json`.

Usage::

    python -m samantha_server.eval.parity_replay \\
        --subset screening \\
        --corpus "$SAMANTHA_POC_CORPUS_PATH" \\
        --out results/samantha-poc-parity \\
        --run-id parity-2026-05-10

`--subset screening` filters to the 33-fixture pinned allow-list from
`samantha_server.eval.screening.SCREENING_SCENARIO_IDS` (sourced from
the upstream `run_phase1_screen.sh`). The screening subset includes
the 8 hallucination-category fixtures (SC-106..SC-113) which trip the
LLM-path branch in `replay()` and cause an MLX client to be
constructed at runtime — running screening parity therefore requires
the configured local LLM to be available. `--subset accumulated_state`
points the corpus directory at the `accumulated_state/` subdirectory
(no id filter — the directory is the subset).

Receipt-store isolation: receipts persist to
`<out>/<run_id>.receipts.sqlite` so the parity sweep produces an
auditable per-run trail without touching the configured production
receipts store. The DB lives next to the JSON/Markdown artifacts.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import logging
import os
import re
import sys
import uuid
from pathlib import Path

from opentelemetry import trace

from samantha_server.eval.parity_report import write_parity_report
from samantha_server.eval.screening import SCREENING_SCENARIO_IDS
from samantha_server.observability.otel import (
    ENVIRONMENT_PARITY,
    PARENT_SPAN_NAME,
    stamp_trace_attributes,
)
from samantha_server.observability.trace_context import TraceContext
from samantha_server.scenarios.replay import replay

_log = logging.getLogger(__name__)

# Filename-safe stem: alphanumeric, underscore, dash, dot. Rejects path
# separators (so `--run-id ../foo` cannot escape `--out`) and shell
# metacharacters that would surprise downstream consumers.
_RUN_ID_PATTERN: re.Pattern[str] = re.compile(r"^[A-Za-z0-9_.-]+$")

_DEFAULT_CORPUS_ENV: str = "SAMANTHA_POC_CORPUS_PATH"
_DEFAULT_CORPUS_FALLBACK: str = "poc-corpus/scenarios"
# results/<run-class>/ matches the project convention (sibling: results/perf,
# results/regression) and mirrors the upstream POC's results/<run-class>/ shape.
# `results/` is gitignored — run artifacts (JSON / Markdown / receipts.sqlite)
# stay out of the tree by default.
_DEFAULT_OUT_DIR: str = "results/samantha-poc-parity"


def main(argv: list[str] | None = None) -> int:
    """CLI entry. Returns 0 on success, 2 on argparse failure, 1 on
    handled runtime errors (missing corpus, malformed skiplist, unsafe
    run_id, empty post-filter set, write failures)."""
    parser = argparse.ArgumentParser(
        prog="python -m samantha_server.eval.parity_replay",
        description="Replay a samantha POC subset and emit a comparison report.",
    )
    parser.add_argument(
        "--subset",
        choices=("screening", "accumulated_state"),
        required=True,
        help="Which POC subset to replay.",
    )
    parser.add_argument(
        "--corpus",
        type=Path,
        default=None,
        help=(
            f"Path to the samantha POC corpus root. Defaults to "
            f"`${_DEFAULT_CORPUS_ENV}` env var, then to "
            f"`{_DEFAULT_CORPUS_FALLBACK}`."
        ),
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(_DEFAULT_OUT_DIR),
        help=f"Output directory for the parity report. Default: `{_DEFAULT_OUT_DIR}`.",
    )
    parser.add_argument(
        "--run-id",
        type=str,
        default=None,
        help="Run identifier (used as the artifact filename stem). "
        "Default: timestamp + short UUID.",
    )

    args = parser.parse_args(argv)

    # Load `.env` before importing config so secrets / model paths are present
    # without an explicit `set -a; source .env; set +a` step. See _cli.py.
    # ORDER IS LOAD-BEARING: `import samantha_server.config` below MUST stay
    # inside main() (after load_dotenv_for_cli). Do not hoist the cfg import
    # back to module level — config.py reads os.environ at import time, and
    # a module-scope import would fire before the dotenv values are present.
    from samantha_server._cli import load_dotenv_for_cli

    load_dotenv_for_cli()
    import samantha_server.config as cfg

    try:
        corpus = _resolve_corpus_root(args.corpus)
        out_dir: Path = args.out
        run_id: str = args.run_id or _default_run_id()

        if not _RUN_ID_PATTERN.match(run_id):
            raise ValueError(
                f"--run-id {run_id!r} contains characters outside "
                "[A-Za-z0-9_.-]; the value is used as a filename stem and "
                "must not contain path separators or shell metacharacters."
            )

        if not corpus.exists():
            raise FileNotFoundError(
                f"Corpus directory does not exist: {corpus}. "
                f"Set ${_DEFAULT_CORPUS_ENV} or pass --corpus."
            )

        if args.subset == "screening":
            corpus_for_replay = corpus
            # set() so the type matches replay()'s `set[str] | None` annotation;
            # the underlying pinned constant stays frozen.
            include_ids: set[str] | None = set(SCREENING_SCENARIO_IDS)
        else:
            # accumulated_state subset: the directory itself is the subset.
            corpus_for_replay = corpus / "accumulated_state"
            include_ids = None

        out_dir.mkdir(parents=True, exist_ok=True)
        receipts_db = out_dir / f"{run_id}.receipts.sqlite"

        # Stamp environment=parity on the parity-replay parent span so
        # dashboards can discriminate "comparing-to-published-numbers" runs
        # from regular replay sweeps. When no TracerProvider is configured this
        # resolves to a no-op tracer; the with-block remains correct.
        # GH-183: emits via the canonical langfuse.* / metadata.* surface (the
        # legacy samantha.environment key was dropped in the schema dedupe).
        tracer = trace.get_tracer("samantha_server.eval.parity_replay")
        with tracer.start_as_current_span(PARENT_SPAN_NAME) as span:
            stamp_trace_attributes(span, TraceContext(environment=ENVIRONMENT_PARITY))
            report = replay(
                corpus_for_replay,
                include_scenario_ids=include_ids,
                receipts_db_path=receipts_db,
                # Parity replay must surface every gap in one pass: a single
                # unmodelled flag (e.g. SC-092's FIXATION_WARNING / GH-169)
                # would otherwise halt the sweep at the first deterministic
                # exception and hide the rest of the diff. Errored steps
                # land as status="error" StepVerdicts in the report.
                re_raise_on_deterministic_error=False,
            )

        if include_ids is not None:
            seen_ids = {v.scenario_id for v in report.scenario_verdicts}
            missing = include_ids - seen_ids
            if missing:
                _log.warning(
                    "parity-replay: %d include_scenario_ids did not match any "
                    "scenario in the corpus and were silently dropped: %s",
                    len(missing),
                    sorted(missing),
                )

        if report.overall_total == 0:
            raise ValueError(
                f"Parity replay produced zero scenarios (corpus={corpus_for_replay}, "
                f"include_scenario_ids={'set' if include_ids is not None else 'None'}). "
                "An empty corpus or fully-mismatched include filter would otherwise "
                "yield a green report over zero scenarios — refusing to emit."
            )

        model_id = cfg.LLM_MODEL_NAME
        write_parity_report({model_id: report}, out_dir, run_id=run_id)
        return 0
    except (FileNotFoundError, ValueError, OSError) as exc:
        # Surface a one-line message to stderr instead of an opaque traceback.
        # MisconfiguredEnvironmentError is a ValueError subclass; covered here.
        print(f"parity-replay error: {exc}", file=sys.stderr)
        return 1


def _resolve_corpus_root(cli_value: Path | None) -> Path:
    if cli_value is not None:
        return cli_value
    env_value = os.environ.get(_DEFAULT_CORPUS_ENV)
    raw = env_value if env_value else _DEFAULT_CORPUS_FALLBACK
    return Path(raw).expanduser()


def _default_run_id() -> str:
    stamp = _dt.datetime.now(_dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"parity-{stamp}-{uuid.uuid4().hex[:8]}"


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

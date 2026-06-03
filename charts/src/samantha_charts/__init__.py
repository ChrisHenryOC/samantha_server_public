"""samantha-charts: charting pipeline for samantha_server metrics.

Side effect on import: sets the matplotlib backend to ``Agg`` (non-interactive).
Done here so that any consumer of ``samantha_charts.*`` gets the headless
backend automatically, regardless of which submodule is the entry point.
Charts are static PNG output by design — no interactive GUI involved.

The ``use()`` call is a no-op if a backend has already been chosen earlier
in the process (e.g., by an outer test harness). Importing ``samantha_charts``
inside a Jupyter session won't override the inline / widget backend.
"""

import matplotlib

matplotlib.use("Agg")

__version__ = "0.1.0"

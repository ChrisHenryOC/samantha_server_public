"""Tests for the --n-sweeps CLI argument.

Verifies:
- --n-sweeps=5 sets args.n_sweeps == 5
- Default value is 1
- Zero and negative values are rejected with SystemExit
"""

from __future__ import annotations

import argparse

import pytest


def _build_parser() -> argparse.ArgumentParser:
    """Import and return the argument parser used by replay main()."""
    from samantha_server.scenarios.replay import _build_replay_parser

    return _build_replay_parser()


def test_n_sweeps_arg_accepts_positive_int() -> None:
    """--n-sweeps=5 parses to args.n_sweeps == 5."""
    parser = _build_parser()
    args = parser.parse_args(["--n-sweeps=5", "/some/dir"])
    assert args.n_sweeps == 5


def test_n_sweeps_default_is_1() -> None:
    """When --n-sweeps is omitted, args.n_sweeps defaults to 1."""
    parser = _build_parser()
    args = parser.parse_args(["/some/dir"])
    assert args.n_sweeps == 1


def test_n_sweeps_rejects_zero() -> None:
    """--n-sweeps=0 is rejected with SystemExit."""
    parser = _build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--n-sweeps=0", "/some/dir"])


def test_n_sweeps_rejects_negative() -> None:
    """--n-sweeps=-1 is rejected with SystemExit."""
    parser = _build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--n-sweeps=-1", "/some/dir"])


def test_n_sweeps_rejects_non_integer_string() -> None:
    """--n-sweeps=abc is rejected with SystemExit."""
    parser = _build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--n-sweeps=abc", "/some/dir"])


def test_n_sweeps_rejects_float_string() -> None:
    """--n-sweeps=1.5 is rejected with SystemExit."""
    parser = _build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--n-sweeps=1.5", "/some/dir"])

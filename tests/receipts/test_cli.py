"""Slice 13: CLI gen-key test.

python -m samantha_server.receipts.signing --gen-key
prints a 64-char lowercase hex string to stdout and exits 0.
"""

from __future__ import annotations

import subprocess
import sys


def test_gen_key_prints_64_char_hex() -> None:
    """--gen-key prints a 64-char lowercase hex string to stdout, exit 0."""
    result = subprocess.run(
        [sys.executable, "-m", "samantha_server.receipts.signing", "--gen-key"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"Expected exit 0, got {result.returncode}.\nstderr: {result.stderr}"
    )
    output = result.stdout.strip()
    assert len(output) == 64, f"Expected 64-char hex output, got {len(output)} chars: {output!r}"
    assert output == output.lower(), f"Expected lowercase hex, got: {output!r}"
    assert all(c in "0123456789abcdef" for c in output), f"Expected all hex chars, got: {output!r}"


def test_gen_key_each_call_produces_unique_key() -> None:
    """Two consecutive --gen-key calls produce different keys (randomness check)."""
    keys = set()
    for _ in range(3):
        result = subprocess.run(
            [sys.executable, "-m", "samantha_server.receipts.signing", "--gen-key"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        keys.add(result.stdout.strip())

    assert len(keys) == 3, "gen-key produced duplicate keys — randomness issue"


def test_gen_key_uses_libsodium_randombytes() -> None:
    """The generated key is a valid Ed25519 seed (can be loaded by nacl.signing)."""
    import nacl.signing

    result = subprocess.run(
        [sys.executable, "-m", "samantha_server.receipts.signing", "--gen-key"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    seed_hex = result.stdout.strip()
    seed = bytes.fromhex(seed_hex)

    # If this doesn't raise, the seed is a valid 32-byte Ed25519 seed
    key = nacl.signing.SigningKey(seed)
    assert len(bytes(key)) == 32

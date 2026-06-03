"""Slice 4: confirm PyNaCl is available in the project's dependency set."""


def test_nacl_signing_is_importable() -> None:
    """nacl.signing must be importable; fails until pynacl is added to pyproject.toml."""
    import nacl.signing  # noqa: F401

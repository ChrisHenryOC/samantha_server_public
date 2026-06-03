"""Smoke tests: verify all package and subpackage imports work."""


def test_import_samantha_server() -> None:
    import samantha_server  # noqa: F401


def test_import_primitives() -> None:
    import samantha_server.primitives  # noqa: F401


def test_import_models() -> None:
    import samantha_server.models  # noqa: F401


def test_import_rules() -> None:
    import samantha_server.rules  # noqa: F401


def test_import_engine() -> None:
    import samantha_server.engine  # noqa: F401


def test_import_scenarios() -> None:
    import samantha_server.scenarios  # noqa: F401


def test_import_receipts() -> None:
    import samantha_server.receipts  # noqa: F401

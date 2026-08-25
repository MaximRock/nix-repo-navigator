"""Smoke test: the package is importable and reports its version."""


def test_package_imports() -> None:
    import repo_navigator

    assert repo_navigator.__version__ == "0.1.0"

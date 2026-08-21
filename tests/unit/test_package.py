from envresearch import __version__


def test_package_exports_version() -> None:
    assert __version__ == "0.2.0"

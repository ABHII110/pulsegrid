import pulsegrid


def test_version_is_exposed() -> None:
    assert pulsegrid.__version__ == "0.1.0"

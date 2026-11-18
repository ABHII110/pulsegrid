"""PulseGrid: uptime monitoring with scheduled probes, incidents and webhook alerts."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("pulsegrid")
except PackageNotFoundError:  # running from a source tree that is not installed
    __version__ = "0.0.0"

__all__ = ["__version__"]

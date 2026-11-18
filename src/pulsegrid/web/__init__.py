"""Static web assets served by the API (the landing page)."""

from functools import lru_cache
from importlib.resources import files


@lru_cache(maxsize=1)
def landing_page() -> str:
    """Return the landing page HTML shipped inside the package."""
    return files(__package__).joinpath("index.html").read_text(encoding="utf-8")

"""Material screening agent."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("material-screening-agent")
except PackageNotFoundError:  # pragma: no cover - editable source import
    __version__ = "0.1.0"

__all__ = ["__version__"]


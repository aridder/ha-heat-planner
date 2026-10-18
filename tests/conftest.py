"""Fixtures."""
import pytest

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    return


# The frontend wheel is not installed in the test venv; provide a stub so the
# `frontend` dependency can set up.
from pathlib import Path
import sys
import tempfile
import types

if "hass_frontend" not in sys.modules:
    _root = Path(tempfile.mkdtemp())
    for sub in ("static", "frontend_latest", "frontend_es5", "static/icons"):
        (_root / sub).mkdir(parents=True, exist_ok=True)
    (_root / "index.html").write_text("<html></html>")
    _mod = types.ModuleType("hass_frontend")
    _mod.where = lambda: _root
    sys.modules["hass_frontend"] = _mod

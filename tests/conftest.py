from __future__ import annotations

import sys
from pathlib import Path

SUBMODULE_ROOT = Path(__file__).resolve().parents[1]
HOST_ROOT = SUBMODULE_ROOT.parent
sys.path.insert(0, str(SUBMODULE_ROOT / "src"))
sys.path.insert(0, str(HOST_ROOT))


import pytest
from types import SimpleNamespace


@pytest.fixture(autouse=True)
def session_signing_config(monkeypatch):
    from game_host import jwt as sessions

    config = SimpleNamespace(jwt_secret="test-session-signing-key-not-for-production-0123456789")
    monkeypatch.setattr(sessions, "get_driver", lambda: SimpleNamespace(config=config))

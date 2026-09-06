from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

TESTS_ROOT = Path(__file__).resolve().parent
PLUGIN_ROOT = TESTS_ROOT.parent
PLUGIN_PARENT = PLUGIN_ROOT.parent
ASTRBOT_ROOT = PLUGIN_PARENT.parent.parent

for import_root in (PLUGIN_PARENT, ASTRBOT_ROOT):
    import_root_text = str(import_root)
    if import_root_text not in sys.path:
        sys.path.insert(0, import_root_text)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def fixture_dir() -> Path:
    return TESTS_ROOT / "fixtures"


@pytest.fixture(scope="session")
def fixture_loader(fixture_dir: Path):
    def load(name: str) -> Any:
        return _load_json(fixture_dir / name)

    return load


@pytest.fixture()
def overview_data(fixture_loader):
    return fixture_loader("overview.json")


@pytest.fixture()
def status_data(fixture_loader):
    return fixture_loader("status.json")


@pytest.fixture()
def history_data(fixture_loader):
    return fixture_loader("history_1h.json")


@pytest.fixture()
def history_gap_data(fixture_loader):
    return fixture_loader("history_gap.json")


@pytest.fixture()
def config_data(fixture_loader):
    return fixture_loader("config.json")


@pytest.fixture()
def webhook_data(fixture_loader):
    return fixture_loader("webhook.json")


@pytest.fixture()
def client_data(fixture_loader):
    return fixture_loader("client.json")


@pytest.fixture()
def models_data(fixture_loader):
    return fixture_loader("models.json")


@pytest.fixture()
def query_data(fixture_loader):
    return fixture_loader("query.json")


@pytest.fixture()
def rendering_data(fixture_loader):
    return fixture_loader("rendering.json")


@pytest.fixture()
def container_history_data(fixture_loader):
    return fixture_loader("container_history.json")

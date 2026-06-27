import pytest
from fastapi.testclient import TestClient

import main


@pytest.fixture
def client():
    yield TestClient(main.app)
    main.app.dependency_overrides.clear()  # nach jedem Test sauber zurücksetzen

from pathlib import Path

import pytest

from codebase_kg.ids import call_id, entity_id, file_id, repository_id, stable_id

FIXTURE = Path(__file__).resolve().parents[1] / "examples" / "sample_repo"


@pytest.fixture
def fixture_repo() -> Path:
    return FIXTURE

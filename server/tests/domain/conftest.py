"""The domain rules are pure, so these tests need no database."""
import pytest


@pytest.fixture(autouse=True)
def clean():
    """Replaces the parent's database cleanup."""
    return None

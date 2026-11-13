import pytest


@pytest.mark.skip(reason="TODO(stage 1-4): no generator yet")
def test_same_seed_gives_same_dataset() -> None:
    """Two generator runs with the same seed must produce identical runs and labels."""

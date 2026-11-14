import pytest


@pytest.mark.skip(
    reason="TODO(stages 2-4): no run generator yet (households: unit/test_households.py)"
)
def test_same_seed_gives_same_dataset() -> None:
    """Two generator runs with the same seed must produce identical runs and labels."""

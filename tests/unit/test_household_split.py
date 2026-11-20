from advisor_qa.world import fixture as fx
from advisor_qa.world.split import SplitParams, assign_households

IDS = [fx.FIXTURE_HOUSEHOLD_ID] + [f"HH-{i:06d}" for i in range(1, 750)]


def test_counts_fixture_pinned_and_disjoint() -> None:
    df = assign_households(IDS, 7, SplitParams())
    counts = df["split"].value_counts().to_dict()
    assert counts == {"train": 450, "val": 150, "test": 150}
    assert df.set_index("household_id").loc[fx.FIXTURE_HOUSEHOLD_ID, "split"] == "train"
    assert df["household_id"].is_unique  # each household in exactly one split


def test_seeded_and_stable_when_households_are_added() -> None:
    a = assign_households(IDS, 7, SplitParams())
    assert a.equals(assign_households(IDS, 7, SplitParams()))
    assert not a.equals(assign_households(IDS, 8, SplitParams()))
    # each household's draw is keyed by its id: the order of the rest does not change
    b = assign_households(IDS + ["HH-000750"], 7, SplitParams()).set_index("household_id")
    same = (a.set_index("household_id")["split"] == b.loc[a["household_id"], "split"]).mean()
    assert same > 0.98

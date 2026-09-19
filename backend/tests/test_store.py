"""Persist source records and progress as one transaction."""

from panel.store import Store


def test_checkpoint_and_changed_record_survive_restart(tmp_path):
    path = tmp_path / "calls.sqlite"
    store = Store(path)
    store.initialize()
    generation = store.generation("source", 100)
    row = {"OBJECTID": 1, "Event_Number": "PD1", "Call_Received": 1770000000000,
           "Disposition_Description": None, "ZONE_": "1", "new_attribute": "retained"}
    store.commit_page(generation, "range:100", [row], 1)
    assert store.checkpoint(generation, "range:100") == 1
    assert store.version() == 1
    store.commit_page(generation, "range:100", [row], 1)
    assert store.version() == 1
    store.close()
    store = Store(path)
    assert store.checkpoint(generation, "range:100") == 1
    row["Disposition_Description"] = "REPORT TAKEN"
    store.commit_page(generation, "range:100", [row], 1)
    assert store.version() == 2
    saved = store.query({}, 50, None)
    assert saved[0]["Disposition_Description"] == "REPORT TAKEN"
    assert saved[0]["raw"]["new_attribute"] == "retained"
    store.close()


def test_event_number_is_not_a_unique_key(tmp_path):
    store = Store(tmp_path / "calls.sqlite")
    store.initialize()
    generation = store.generation("source", 2)
    store.commit_page(generation, "live", [
        {"OBJECTID": 1, "Event_Number": "PD1", "Unit_Dispatched": "A"},
        {"OBJECTID": 2, "Event_Number": "PD1", "Unit_Dispatched": "B"},
    ], 2)
    assert len(store.query({}, 50, None)) == 2
    store.close()

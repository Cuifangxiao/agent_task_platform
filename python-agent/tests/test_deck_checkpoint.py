from langgraph.checkpoint.base import empty_checkpoint

from core.deck.checkpoint import PersistentSQLiteSaver


def test_sqlite_checkpointer_survives_reopen(tmp_path):
    path = tmp_path / "checkpoints.sqlite3"
    config = {"configurable": {"thread_id": "job-1", "checkpoint_ns": ""}}
    checkpoint = empty_checkpoint()
    checkpoint["id"] = "0001"
    checkpoint["channel_values"] = {"outline": "kept"}
    checkpoint["channel_versions"] = {"outline": "1"}

    saver = PersistentSQLiteSaver(path)
    saved_config = saver.put(config, checkpoint, {"source": "input", "step": 1, "parents": {}}, {"outline": "1"})
    saver.close()

    reopened = PersistentSQLiteSaver(path)
    restored = reopened.get_tuple(saved_config)
    assert restored is not None
    assert restored.checkpoint["channel_values"]["outline"] == "kept"
    reopened.close()

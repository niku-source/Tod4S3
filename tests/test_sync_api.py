import sync_api
import web_store


def test_deliveries_only_return_tod4s3_bot_source(tmp_path, monkeypatch):
    monkeypatch.setattr(sync_api, "STATE_FILE", tmp_path / "sync_app.json")
    monkeypatch.setattr(sync_api, "user_id_for_token", lambda _token: 42)

    sync_api.record_delivery(
        42,
        "task-1",
        "archivo.bin",
        4,
        ["https://s3.todus.cu/stream/parte1"],
    )

    state = sync_api._read()
    state["deliveries"]["42"].insert(
        0,
        {
            "task_id": "private-chat-item",
            "source": "other_source",
            "filename": "privado.bin",
            "size": 9,
            "parts": ["https://example.invalid/private"],
        },
    )
    sync_api._write(state)

    rows = sync_api.deliveries_for_token("irrelevant-test-token")
    assert len(rows) == 1
    assert rows[0]["task_id"] == "task-1"
    assert rows[0]["source"] == "tod4s3_bot"


def _prepare_delivery(tmp_path, monkeypatch, parts):
    monkeypatch.setattr(sync_api, "STATE_FILE", tmp_path / "sync_app.json")
    monkeypatch.setattr(sync_api, "user_id_for_token", lambda _token: 42)
    sync_api.record_delivery(42, "task-delete", "archivo.bin", 8, parts)


def test_delete_delivery_success_removes_remote_parts_and_local_row(tmp_path, monkeypatch):
    urls = [
        "https://s3.todus.cu/stream/users/tg_42/telegram/parte-1.bin",
        "https://s3.todus.cu/stream/users/tg_42/telegram/parte-2.bin",
    ]
    _prepare_delivery(tmp_path, monkeypatch, urls)
    deleted = []
    monkeypatch.setattr(web_store, "delete_from_todus_s3", deleted.append)

    payload, status = web_store.delete_delivery_for_token("app-token", "task-delete")

    assert status == 200
    assert payload == {"ok": True, "task_id": "task-delete", "deleted_parts": 2}
    assert deleted == urls
    assert sync_api._read()["deliveries"]["42"] == []


def test_delete_delivery_rejects_url_outside_user_namespace(tmp_path, monkeypatch):
    url = "https://s3.todus.cu/stream/users/tg_41/telegram/foreign.bin"
    _prepare_delivery(tmp_path, monkeypatch, [url])
    deleted = []
    monkeypatch.setattr(web_store, "delete_from_todus_s3", deleted.append)

    payload, status = web_store.delete_delivery_for_token("app-token", "task-delete")

    assert status == 409
    assert payload["error"] == "invalid_delivery_url"
    assert deleted == []
    assert sync_api._read()["deliveries"]["42"]


def test_delete_delivery_failure_keeps_local_row_for_retry(tmp_path, monkeypatch):
    url = "https://s3.todus.cu/stream/users/tg_42/telegram/parte.bin"
    _prepare_delivery(tmp_path, monkeypatch, [url])

    def fail_delete(_url):
        raise RuntimeError("simulated ToDus failure")

    monkeypatch.setattr(web_store, "delete_from_todus_s3", fail_delete)
    payload, status = web_store.delete_delivery_for_token("app-token", "task-delete")

    assert status == 502
    assert payload["error"] == "delete_failed"
    assert sync_api._read()["deliveries"]["42"][0]["task_id"] == "task-delete"


def test_delete_delivery_rejects_legacy_row_without_task_id(tmp_path, monkeypatch):
    monkeypatch.setattr(sync_api, "STATE_FILE", tmp_path / "sync_app.json")
    monkeypatch.setattr(sync_api, "user_id_for_token", lambda _token: 42)
    state = sync_api._read()
    state["deliveries"]["42"] = [{
        "source": "tod4s3_bot",
        "parts": ["https://s3.todus.cu/stream/users/tg_42/telegram/legacy.bin"],
    }]
    sync_api._write(state)
    deleted = []
    monkeypatch.setattr(web_store, "delete_from_todus_s3", deleted.append)

    payload, status = web_store.delete_delivery_for_token("app-token", "task-unknown")

    assert status == 409
    assert payload["error"] == "legacy_delivery_incomplete"
    assert "sin task_id" in payload["message"]
    assert deleted == []


def test_delete_delivery_accepts_legacy_row_without_source(tmp_path, monkeypatch):
    url = "https://s3.todus.cu/stream/users/tg_42/telegram/legacy.bin"
    monkeypatch.setattr(sync_api, "STATE_FILE", tmp_path / "sync_app.json")
    monkeypatch.setattr(sync_api, "user_id_for_token", lambda _token: 42)
    state = sync_api._read()
    state["deliveries"]["42"] = [{
        "task_id": "task-legacy-source",
        "filename": "archivo-antiguo.bin",
        "size": 8,
        "parts": [url],
    }]
    sync_api._write(state)
    deleted = []
    monkeypatch.setattr(web_store, "delete_from_todus_s3", deleted.append)

    payload, status = web_store.delete_delivery_for_token("app-token", "task-legacy-source")

    assert status == 200
    assert payload["deleted_parts"] == 1
    assert deleted == [url]
    assert sync_api._read()["deliveries"]["42"] == []

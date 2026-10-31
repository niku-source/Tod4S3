from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import todus_s3_storage as storage


def test_build_object_key_uses_custom_s3_destination_prefix():
    timestamp = datetime(2026, 10, 30, 14, 5, 6, tzinfo=timezone.utc)
    key = storage.build_object_key(
        73421,
        timestamp,
        "archivo de prueba.zip",
        object_prefix="users/df",
    )

    assert key.startswith("users/df/")
    assert "/users/tg_73421/telegram/" not in f"/{key}"
    assert key.endswith("_archivo_de_prueba.zip")


def test_build_object_key_rejects_path_traversal_in_custom_prefix():
    with pytest.raises(ValueError):
        storage.build_object_key(73421, filename="archivo.zip", object_prefix="users/../other")


def test_upload_uses_the_requested_users_df_url_without_network(tmp_path: Path):
    local_file = tmp_path / "archivo.bin"
    local_file.write_bytes(b"datos de prueba")

    class FakeSession:
        def __init__(self):
            self.url = None

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def put(self, url, **kwargs):
            self.url = url
            return SimpleNamespace(status_code=200)

    fake_session = FakeSession()
    with patch.object(storage.requests, "Session", return_value=fake_session):
        url, size = storage.upload_to_todus_s3(
            local_file,
            "archivo.bin",
            73421,
            object_prefix="users/df",
        )

    assert url == fake_session.url
    assert url.startswith("https://s3.todus.cu/stream/users/df/")
    assert size == len(b"datos de prueba")

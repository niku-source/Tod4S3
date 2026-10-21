import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from todus_s3_storage import (
    ToDusS3UploadError,
    _content_disposition,
    build_object_key,
    delete_from_todus_s3,
    upload_to_todus_s3,
)


class StorageExtensionTests(unittest.TestCase):
    def test_object_keys_preserve_apk_and_mp4_extensions(self):
        self.assertTrue(build_object_key(12345, filename="app.apk").endswith(".apk"))
        self.assertTrue(build_object_key(12345, filename="video.mp4").endswith(".mp4"))
        self.assertTrue(build_object_key(12345).endswith(".bin"))
        self.assertTrue(build_object_key(12345, filename="bad name.💣").endswith(".bin"))

    def _upload(self, filename, expected_suffix, expected_mime):
        class FakeResponse:
            status_code = 200

        class FakeSession:
            headers = None

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def put(self, _url, **kwargs):
                self.headers = kwargs["headers"]
                return FakeResponse()

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / filename
            path.write_bytes(b"file-content")
            session = FakeSession()
            with patch("todus_s3_storage.requests.Session", return_value=session):
                url, size = upload_to_todus_s3(path, filename, 12345)

        self.assertTrue(url.endswith(expected_suffix))
        self.assertEqual(size, len(b"file-content"))
        self.assertEqual(session.headers["Content-Type"], expected_mime)
        self.assertIn(filename, session.headers["Content-Disposition"])

    def test_apk_upload_keeps_extension_and_android_package_mime(self):
        self._upload("my-app.apk", ".apk", "application/vnd.android.package-archive")

    def test_mp4_upload_keeps_extension_and_video_mime(self):
        self._upload("clip.mp4", ".mp4", "video/mp4")

    def test_apk_is_download_attachment_and_mp4_is_inline(self):
        self.assertTrue(_content_disposition("app.apk").startswith("attachment;"))
        self.assertTrue(_content_disposition("clip.mp4").startswith("inline;"))

    def test_delete_requires_object_to_be_gone(self):
        class Response:
            def __init__(self, status_code):
                self.status_code = status_code

            def close(self):
                pass

        with patch("todus_s3_storage.requests.delete", return_value=Response(204)), \
             patch("todus_s3_storage.requests.head", return_value=Response(404)):
            delete_from_todus_s3("https://s3.todus.cu/stream/users/tg_1/telegram/file.bin")

        with patch("todus_s3_storage.requests.delete", return_value=Response(204)), \
             patch("todus_s3_storage.requests.head", return_value=Response(200)), \
             patch("todus_s3_storage.time.sleep"):
            with self.assertRaises(ToDusS3UploadError):
                delete_from_todus_s3("https://s3.todus.cu/stream/users/tg_1/telegram/file.bin")


if __name__ == "__main__":
    unittest.main()

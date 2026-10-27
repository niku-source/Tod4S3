import socket
import tempfile
import unittest
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile
from unittest.mock import MagicMock, Mock, patch

import yt_dlp
from yt_dlp.utils import DownloadError

from bot import download_to_temp, filename_from_response, normalize_apk_filename, validate_downloaded_file
from platform_downloads import (
    _check_public_dns_target,
    download_platform_video,
    is_play_store_page,
    is_supported_platform_url,
)


class PlatformRecognitionTests(unittest.TestCase):
    def test_recognizes_youtube_and_facebook_pages_and_short_links(self):
        self.assertTrue(is_supported_platform_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ"))
        self.assertTrue(is_supported_platform_url("https://www.facebook.com/watch/?v=123456789"))
        self.assertTrue(is_supported_platform_url("https://fb.watch/abc123/"))
        self.assertTrue(is_supported_platform_url("https://vm.tiktok.com/abc123/"))
        self.assertTrue(is_supported_platform_url("https://www.instagram.com/reel/abc123/"))

    def test_recognizes_all_advertised_platform_hosts(self):
        urls = [
            "https://www.tiktok.com/@user/video/123456789",
            "https://twitter.com/user/status/123456789",
            "https://x.com/user/status/123456789",
            "https://www.reddit.com/r/test/comments/abc123/test/",
            "https://www.twitch.tv/videos/123456789",
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertTrue(is_supported_platform_url(url))

    def test_does_not_route_direct_file_urls_through_extractor(self):
        self.assertFalse(is_supported_platform_url("https://files.example.org/movie.mp4"))

    def test_recognizes_play_store_listing_as_not_a_direct_apk(self):
        self.assertTrue(is_play_store_page("https://play.google.com/store/apps/details?id=org.mozilla.firefox"))
        self.assertFalse(is_play_store_page("https://cdn.example.org/apps/firefox.apk"))

    def test_rejects_private_ip_before_platform_extractor_runs(self):
        with self.assertRaisesRegex(ValueError, "privadas"):
            _check_public_dns_target("https://127.0.0.1/video")

    def test_rejects_domain_that_resolves_to_private_ip(self):
        private_record = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.4", 443))
        with patch("platform_downloads.socket.getaddrinfo", return_value=[private_record]):
            with self.assertRaisesRegex(ValueError, "privada"):
                _check_public_dns_target("https://videos.example.org/watch/123")


class PlatformDownloadTests(unittest.TestCase):
    def test_download_uses_single_mp4_preferred_format_and_reports_progress(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "platform-download"
            ydl = MagicMock()
            captured = {}
            ydl_class = MagicMock()
            ydl_class.return_value.__enter__.return_value = ydl
            progress = Mock()

            def fake_extract(url, download=True):
                self.assertEqual(url, "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
                self.assertTrue(download)
                options = ydl_class.call_args.args[0]
                captured.update(options)
                captured["progress_hooks"][0]({
                    "status": "downloading",
                    "downloaded_bytes": 4,
                    "total_bytes": 8,
                })
                (output / "clip.mp4").write_bytes(b"video")
                return {"title": "clip"}

            ydl.extract_info.side_effect = fake_extract
            with patch("platform_downloads._check_public_dns_target"), patch.object(
                yt_dlp, "YoutubeDL", ydl_class
            ):
                path, name, size = download_platform_video(
                    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                    output,
                    max_bytes=1024,
                    progress_hook=progress,
                )

        self.assertEqual(path.name, "clip.mp4")
        self.assertEqual(name, "clip.mp4")
        self.assertEqual(size, 5)
        self.assertTrue(captured["noplaylist"])
        self.assertIn("ext=mp4", captured["format"])
        self.assertEqual(captured["max_filesize"], 1024)
        self.assertNotIn("http_headers", captured)
        self.assertNotIn("cookiefile", captured)
        ydl.extract_info.assert_called_once()
        progress.assert_called_once_with(4, 8)

    def test_login_required_is_rejected_without_loading_cookies_after_public_fallbacks(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "platform-download"
            ydl = MagicMock()
            ydl.extract_info.side_effect = DownloadError("Sign in to confirm you are not a bot")
            ydl_class = MagicMock()
            ydl_class.return_value.__enter__.return_value = ydl

            with patch("platform_downloads._check_public_dns_target"), patch.object(
                yt_dlp, "YoutubeDL", ydl_class
            ), patch.dict(os.environ, {"YTDLP_COOKIES_FILE": "/tmp/should-not-be-used.txt"}):
                with self.assertRaisesRegex(ValueError, "YouTube bloqueó esta descarga"):
                    download_platform_video(
                        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                        output,
                        max_bytes=1024,
                    )

        options = ydl_class.call_args.args[0]
        self.assertNotIn("cookiefile", options)
        self.assertNotIn("http_headers", options)
        self.assertEqual(ydl.extract_info.call_count, 4)

    def test_short_youtube_link_retries_403_with_web_client(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "platform-download"
            first = MagicMock()
            first.extract_info.side_effect = DownloadError("unable to download video data: HTTP Error 403: Forbidden")
            second = MagicMock()

            def successful_extract(_url, download=True):
                self.assertTrue(download)
                (output / "clip.mp4").write_bytes(b"video")

            second.extract_info.side_effect = successful_extract
            ydl_class = MagicMock()
            ydl_class.return_value.__enter__.side_effect = [first, second]

            with patch("platform_downloads._check_public_dns_target"), patch.object(
                yt_dlp, "YoutubeDL", ydl_class
            ):
                path, name, size = download_platform_video(
                    "https://youtu.be/dQw4w9WgXcQ",
                    output,
                    max_bytes=1024,
                )

        self.assertEqual((path.name, name, size), ("clip.mp4", "clip.mp4", 5))
        self.assertEqual(ydl_class.call_count, 2)
        retry_options = ydl_class.call_args.args[0]
        self.assertEqual(retry_options["extractor_args"], {"youtube": {"player_client": ["mweb"]}})

    def test_download_aborts_when_progress_exceeds_limit(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "platform-download"
            ydl = MagicMock()
            ydl_class = MagicMock()
            ydl_class.return_value.__enter__.return_value = ydl

            def fake_extract(_url, download=True):
                options = ydl_class.call_args.args[0]
                options["progress_hooks"][0]({
                    "status": "downloading",
                    "downloaded_bytes": 11,
                    "total_bytes": 11,
                })

            ydl.extract_info.side_effect = fake_extract
            with patch("platform_downloads._check_public_dns_target"), patch.object(
                yt_dlp, "YoutubeDL", ydl_class
            ):
                with self.assertRaisesRegex(ValueError, "supera el límite"):
                    download_platform_video(
                        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                        output,
                        max_bytes=10,
                    )


class WebpageResponseTests(unittest.IsolatedAsyncioTestCase):
    async def test_play_store_listing_explains_that_an_apk_is_required(self):
        with self.assertRaisesRegex(ValueError, "no un APK directo"):
            await download_to_temp(
                object(),
                "https://play.google.com/store/apps/details?id=org.mozilla.firefox",
            )

    async def test_html_page_is_not_saved_as_a_small_video_file(self):
        class FakeContent:
            async def iter_chunked(self, _size):
                yield b"<html>not a video</html>"

        class FakeResponse:
            status = 200
            content_length = 24
            headers = {"Content-Type": "text/html; charset=utf-8"}
            url = "https://example.com/watch/123"
            content = FakeContent()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

        class FakeSession:
            def get(self, *_args, **_kwargs):
                return FakeResponse()

        with self.assertRaisesRegex(ValueError, "página web"):
            await download_to_temp(FakeSession(), "https://example.com/watch/123")


class ResponseFilenameTests(unittest.TestCase):
    def test_infers_mp4_from_mime_when_url_has_no_extension(self):
        self.assertEqual(
            filename_from_response({"Content-Type": "video/mp4"}, "https://cdn.example/video/123"),
            "123.mp4",
        )

    def test_infers_apk_from_mime_when_url_has_no_extension(self):
        self.assertEqual(
            filename_from_response(
                {"Content-Type": "application/vnd.android.package-archive"},
                "https://cdn.example/download/123",
            ),
            "123.apk",
        )

    def test_normalizes_real_apk_served_as_zip(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "download.bin"
            with ZipFile(path, "w") as package:
                package.writestr("AndroidManifest.xml", b"binary-manifest")
                package.writestr("classes.dex", b"dex\n035\0")
            self.assertEqual(normalize_apk_filename(path, "download.zip"), "download.apk")
            validate_downloaded_file(path, "download.apk")

    def test_rejects_split_package_as_single_apk(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "bundle.zip"
            with ZipFile(path, "w") as package:
                package.writestr("base.apk", b"split")
            with self.assertRaisesRegex(ValueError, "paquete dividido"):
                normalize_apk_filename(path, "bundle.zip")


if __name__ == "__main__":
    unittest.main()

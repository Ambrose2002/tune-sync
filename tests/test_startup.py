"""Offline checks for configuration and side-effect-free startup."""

import contextlib
import importlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tune_sync.conf import ConfigurationError, load_conf

VALID = {
    "SPOTIFY_CLIENT_ID": "test-client",
    "SPOTIFY_CLIENT_SECRET": "test-secret",
    "SPOTIFY_CALLBACK_URL": "http://127.0.0.1:8000/callback",
}


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        (Path(self.directory.name) / "pyproject.toml").touch()
        self.env_file = Path(self.directory.name) / ".env"
        self.env_file.write_text("\n".join(f'{k}="{v}"' for k, v in VALID.items()))
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_file_loading_and_secret_safe_representation(self):
        settings = load_conf(self.env_file)
        self.assertEqual(settings.spotify_client_id, "test-client")
        self.assertNotIn("test-secret", repr(settings))

    def test_environment_overrides_file(self):
        os.environ["SPOTIFY_CLIENT_ID"] = "override"
        self.assertEqual(load_conf(self.env_file).spotify_client_id, "override")

    def test_empty_override_is_not_silently_replaced(self):
        os.environ["SPOTIFY_CLIENT_ID"] = " "
        with self.assertRaisesRegex(ConfigurationError, "SPOTIFY_CLIENT_ID is required"):
            load_conf(self.env_file)

    def test_missing_settings_are_reported_together(self):
        with self.assertRaises(ConfigurationError) as error:
            load_conf(Path(self.directory.name) / "missing")
        for name in VALID:
            self.assertIn(name, str(error.exception))

    def test_example_credentials_are_rejected(self):
        os.environ["SPOTIFY_CLIENT_SECRET"] = "your-spotify-client-secret"
        with self.assertRaisesRegex(ConfigurationError, "SPOTIFY_CLIENT_SECRET is required"):
            load_conf(self.env_file)

    def test_redirect_validation_and_no_secret_in_errors(self):
        for callback in (
            "http://localhost:8000/callback", "http://example.com/callback",
            "not-a-url", "https://example.com:99999/callback",
            "https://user:test-secret@example.com/callback",
            "https://example.com/callback#fragment", "https://exa mple.com/callback",
        ):
            with self.subTest(callback=callback):
                os.environ["SPOTIFY_CALLBACK_URL"] = callback
                with self.assertRaises(ConfigurationError) as error:
                    load_conf(self.env_file)
                self.assertNotIn("test-secret", str(error.exception))
        for callback in (VALID["SPOTIFY_CALLBACK_URL"],
                         "http://[::1]:8000/callback", "https://example.com/callback"):
            os.environ["SPOTIFY_CALLBACK_URL"] = callback
            self.assertEqual(load_conf(self.env_file).spotify_callback_url, callback)

    def test_default_env_path_is_independent_of_working_directory(self):
        with patch("tune_sync.conf.PROJECT_ROOT", Path(self.directory.name)):
            self.assertEqual(load_conf().spotify_client_id, "test-client")

    def test_import_and_startup_never_initialize_spotify(self):
        with patch.dict(os.environ, VALID), \
             patch("spotipy.Spotify", side_effect=AssertionError("Client initialized")), \
             patch("spotipy.oauth2.SpotifyOAuth", side_effect=AssertionError("OAuth initialized")), \
             patch("tune_sync.conf.PROJECT_ROOT", Path(self.directory.name)), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            from tune_sync import app
            importlib.reload(app)
            self.assertEqual(app.main([]), 0)
            self.assertIn("No login or Spotify API requests", output.getvalue())
            os.environ["SPOTIFY_CLIENT_ID"] = ""
            with contextlib.redirect_stderr(io.StringIO()) as errors:
                self.assertEqual(app.main([]), 1)
            self.assertIn("SPOTIFY_CLIENT_ID", errors.getvalue())
            self.assertNotIn("test-secret", errors.getvalue())


if __name__ == "__main__":
    unittest.main()

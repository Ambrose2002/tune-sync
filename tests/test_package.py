"""Exercise installed entry points and explicitly supplied Spotify clients."""

import contextlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from tune_sync.spotify import create_playlist, create_spotify_client


class PackageTests(unittest.TestCase):
    def test_entry_points_from_another_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / "settings.env"
            env_file.write_text(
                'SPOTIFY_CLIENT_ID="fake"\nSPOTIFY_CLIENT_SECRET="fake"\n'
                'SPOTIFY_CALLBACK_URL="http://127.0.0.1:8000/callback"\n'
            )
            environment = {k: v for k, v in os.environ.items()
                           if not k.startswith("SPOTIFY_") and k != "PYTHONPATH"}
            commands = ([sys.executable, "-m", "tune_sync"],
                        [str(Path(sys.executable).parent / "tune-sync")])
            for command in commands:
                with self.subTest(command=command):
                    result = subprocess.run(
                        [*command, "--env-file", str(env_file)], cwd=directory,
                        env=environment, capture_output=True, text=True, timeout=10,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("No login or Spotify API requests", result.stdout)
                    result = subprocess.run(
                        [*command, "--env-file", str(env_file)], cwd=directory,
                        env={**environment, "SPOTIFY_CLIENT_ID": ""},
                        capture_output=True, text=True, timeout=10,
                    )
                    self.assertEqual(result.returncode, 1)
                    self.assertIn("SPOTIFY_CLIENT_ID", result.stderr)

    def test_explicit_missing_file_does_not_fall_back(self):
        from tune_sync.app import main
        with tempfile.TemporaryDirectory() as directory, \
             contextlib.redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(main(["--env-file", str(Path(directory) / "missing")]), 1)
            self.assertIn("existing file", errors.getvalue())

    def test_client_factory_uses_only_supplied_token(self):
        with patch("tune_sync.spotify.spotipy.Spotify") as client:
            result = create_spotify_client("fake-access-token")
        self.assertIs(result, client.return_value)
        client.assert_called_once_with(auth="fake-access-token", requests_timeout=15, retries=0)

    def test_create_and_search_share_the_supplied_client(self):
        client = Mock()
        client.current_user_playlist_create.return_value = {"id": "destination"}
        client.search.side_effect = [
            {"tracks": {"items": [{"uri": "spotify:track:one"}]}},
            {"tracks": {"items": []}},
        ]
        with patch("tune_sync.spotify.create_spotify_client",
                   side_effect=AssertionError("Unexpected client construction")), \
             contextlib.redirect_stdout(io.StringIO()):
            playlist = create_playlist(client, "Example", ["One", "Missing"])
        self.assertEqual(playlist["id"], "destination")
        self.assertEqual(client.search.call_count, 2)
        client.playlist_add_items.assert_called_once_with(
            "destination", ["spotify:track:one"]
        )


if __name__ == "__main__":
    unittest.main()

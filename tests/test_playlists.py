"""Offline playlist metadata, pagination, and command-output tests."""

from contextlib import contextmanager, redirect_stdout, redirect_stderr
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, call, patch

from spotipy.exceptions import SpotifyException
from tune_sync.app import main, spotify_command
from tune_sync.conf import Conf
from tune_sync.playlists import PlaylistReadError, list_playlists, normalize_playlist


def raw_playlist(identifier, **overrides):
    return {"id": identifier, "name": "Example", "owner": {"id": "me", "display_name": "Me"},
            "public": False, "collaborative": False, "items": {"total": 0},
            "external_urls": {"spotify": f"https://open.spotify.com/playlist/{identifier}"}, **overrides}


class PlaylistTests(unittest.TestCase):
    def test_all_pages_preserve_order_and_include_followed_playlists(self):
        client = Mock()
        client.current_user_playlists.side_effect = [
            {"items": [raw_playlist(str(i)) for i in range(50)],
             "next": "https://api.spotify.com/v1/me/playlists?offset=50&limit=50"},
            {"items": [raw_playlist("followed", owner={"id": "someone-else"})], "next": None},
        ]
        playlists = list_playlists(client, user_id="me")
        self.assertEqual([p.id for p in playlists], [str(i) for i in range(50)] + ["followed"])
        self.assertTrue(playlists[0].is_owned)
        self.assertFalse(playlists[-1].is_owned)
        self.assertEqual(client.current_user_playlists.call_args_list,
                         [call(limit=50, offset=0), call(limit=50, offset=50)])
        client.playlist_items.assert_not_called()
        client.current_user_saved_tracks.assert_not_called()

    def test_empty_library(self):
        client = Mock()
        client.current_user_playlists.return_value = {"items": [], "next": None}
        self.assertEqual(list_playlists(client, user_id="me"), [])

    def test_missing_optional_fields_remain_unknown(self):
        playlist = normalize_playlist({"id": "minimal"}, "me")
        for attribute in ("name", "owner_id", "owner_name", "url", "public",
                          "collaborative", "is_owned", "item_count"):
            self.assertIsNone(getattr(playlist, attribute))
        self.assertEqual(playlist.contents_access, "not_checked")

    def test_legacy_and_new_counts_preserve_zero(self):
        for fields, expected in (({"tracks": {"total": 10}}, 10),
                                 ({"items": {"total": 0}, "tracks": {"total": 10}}, 0),
                                 ({"items": None, "tracks": {"total": 3}}, 3),
                                 ({"items": {"total": True}}, None)):
            with self.subTest(fields=fields):
                self.assertEqual(normalize_playlist({"id": "one", **fields}, "me").item_count, expected)

    def test_collaborative_flag_does_not_claim_contents_access(self):
        playlist = normalize_playlist(raw_playlist("one", collaborative=True, owner={"id": "other"}), "me")
        self.assertTrue(playlist.collaborative)
        self.assertFalse(playlist.is_owned)
        self.assertEqual(playlist.contents_access, "not_checked")

    def test_null_metadata_is_tolerated(self):
        playlist = normalize_playlist(raw_playlist("one", owner=None, external_urls=None, public=None), "me")
        self.assertIsNone(playlist.owner_id)
        self.assertIsNone(playlist.url)
        self.assertIsNone(playlist.public)

    def test_duplicate_ids_fail_rather_than_return_incomplete_listing(self):
        client = Mock()
        client.current_user_playlists.return_value = {"items": [raw_playlist("one"), raw_playlist("one")], "next": None}
        with self.assertRaisesRegex(PlaylistReadError, "repeated"):
            list_playlists(client, user_id="me")

    def test_invalid_pagination_is_rejected_without_following_url(self):
        for next_url in ("https://api.spotify.com/v1/me/playlists?offset=0",
                         "https://evil.example/v1/me/playlists?offset=50",
                         "https://api.spotify.com/v1/me/playlists?offset=100001",
                         "https://api.spotify.com/v1/me/playlists?offset=1&offset=2",
                         "https://api.spotify.com/v1/me/playlists?offset=bad", 15):
            with self.subTest(next_url=next_url):
                client = Mock()
                client.current_user_playlists.return_value = {"items": [], "next": next_url}
                with self.assertRaises(PlaylistReadError):
                    list_playlists(client, user_id="me")
                self.assertEqual(client.current_user_playlists.call_count, 1)

    def test_malformed_page_and_missing_ids_fail(self):
        for page in (None, {}, {"items": []}, {"items": None, "next": None},
                     {"items": [None], "next": None}, {"items": [{"name": "No ID"}], "next": None}):
            with self.subTest(page=page):
                client = Mock()
                client.current_user_playlists.return_value = page
                with self.assertRaises(PlaylistReadError):
                    list_playlists(client, user_id="me")


class PlaylistCommandTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.account = {"id": "me", "display_name": "Example"}
        self.settings = Conf("fake", "http://127.0.0.1:8000/callback")
        @contextmanager
        def connection(*args, **kwargs):
            yield self.client, self.account
        patcher = patch("tune_sync.auth.connected_spotify", connection)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def run_command(self, json_output=False):
        with redirect_stdout(io.StringIO()) as output, redirect_stderr(io.StringIO()) as errors:
            status = spotify_command(self.settings, "playlists", Path(self.directory.name), json_output=json_output)
        return status, output.getvalue(), errors.getvalue()

    def test_json_is_a_single_document_and_contains_no_credentials(self):
        self.client.current_user_playlists.return_value = {"items": [raw_playlist("one")], "next": None}
        status, output, errors = self.run_command(json_output=True)
        self.assertEqual(status, 0, errors)
        result = json.loads(output)
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["playlists"][0]["id"], "one")
        self.assertEqual(result["account"], self.account)
        self.assertNotIn("access_token", output)

    def test_empty_human_output(self):
        self.client.current_user_playlists.return_value = {"items": [], "next": None}
        status, output, errors = self.run_command()
        self.assertEqual(status, 0, errors)
        self.assertIn("0 Spotify playlist(s)", output)

    def test_names_cannot_inject_terminal_control_codes(self):
        self.client.current_user_playlists.return_value = {
            "items": [raw_playlist("one", name="Title\n\x1b[31m")], "next": None}
        status, output, errors = self.run_command()
        self.assertEqual(status, 0, errors)
        self.assertNotIn("\x1b", output)
        self.assertIn(r"Title\n\u001b", output)

    def test_later_page_failure_emits_no_partial_results(self):
        self.client.current_user_playlists.side_effect = [
            {"items": [raw_playlist("one")], "next": "https://api.spotify.com/v1/me/playlists?offset=1"},
            SpotifyException(429, -1, "secret-in-error"),
        ]
        status, output, errors = self.run_command(json_output=True)
        self.assertEqual(status, 1)
        self.assertEqual(output, "")
        self.assertIn("quota", errors)
        self.assertNotIn("secret-in-error", errors)

    def test_missing_connection_is_not_an_empty_library(self):
        from tune_sync.auth import ConnectionError
        with patch("tune_sync.auth.connected_spotify", side_effect=ConnectionError("Spotify is not connected. Run spotify connect")):
            status, output, errors = self.run_command(json_output=True)
        self.assertEqual(status, 1)
        self.assertEqual(output, "")
        self.assertIn("not connected", errors)

    def test_cli_dispatches_json_flag(self):
        with patch("tune_sync.app.load_conf", return_value=self.settings), \
             patch("tune_sync.app.spotify_command", return_value=0) as command:
            self.assertEqual(main(["spotify", "playlists", "--json"]), 0)
        command.assert_called_once_with(self.settings, "playlists", None, json_output=True)

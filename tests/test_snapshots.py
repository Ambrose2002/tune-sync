"""Offline checks for ordered, complete, version-checked playlist snapshots."""

from contextlib import contextmanager, redirect_stdout, redirect_stderr
import io
import json
from unittest import TestCase
from unittest.mock import Mock, call, patch

from spotipy.exceptions import SpotifyException
from tune_sync.app import main, spotify_command
from tune_sync.conf import Conf
from tune_sync.playlists import PlaylistReadError
from tune_sync.snapshots import normalize_entry, read_playlist_snapshot, METADATA_FIELDS

PLAYLIST_ID = "a" * 22


def track(**overrides):
    return {"id": "song", "type": "track", "name": "Song (Live)",
            "artists": [{"name": "First"}, {"name": "Second"}],
            "album": {"name": "Album"}, "duration_ms": 180000, "explicit": False,
            "external_ids": {"isrc": "US1234567890"}, **overrides}


def metadata(**overrides):
    return {"id": PLAYLIST_ID, "name": "Example", "snapshot_id": "version-one",
            "description": "Description", "owner": {"id": "me"}, **overrides}


def page(items, *, offset=0, total=None, next_offset=None):
    return {"items": items, "offset": offset, "total": len(items) if total is None else total,
            "next": None if next_offset is None else
            f"https://api.spotify.com/v1/playlists/{PLAYLIST_ID}/items?offset={next_offset}&limit=50"}


class SnapshotTests(TestCase):
    def setUp(self):
        self.client = Mock()
        self.client.playlist.return_value = metadata()

    def read(self):
        return read_playlist_snapshot(self.client, PLAYLIST_ID, user_id="me", source_country="US")

    def test_multi_page_duplicates_and_order_are_preserved(self):
        self.client.playlist_items.side_effect = [
            page([{"item": track(id=str(i))} for i in range(50)], total=51, next_offset=50),
            page([{"item": track(id="0")}], offset=50, total=51),
        ]
        snapshot = self.read()
        self.assertEqual([entry.position for entry in snapshot.entries], list(range(51)))
        self.assertEqual(snapshot.entries[0].item.id, snapshot.entries[50].item.id)
        self.assertEqual(snapshot.playlist.item_count, 51)
        self.assertEqual(snapshot.playlist.contents_access, "read")
        self.assertEqual(snapshot.description, "Description")
        self.assertEqual(snapshot.source_country, "US")
        self.assertEqual(self.client.playlist.call_args_list,
                         [call(PLAYLIST_ID, fields=METADATA_FIELDS)] * 2)
        self.assertEqual(self.client.playlist_items.call_args_list,
                         [call(PLAYLIST_ID, limit=50, offset=offset, additional_types=("track", "episode"))
                          for offset in (0, 50)])

    def test_metadata_keeps_all_artists_isrc_duration_and_version_words(self):
        entry = normalize_entry({"item": track()}, 2)
        self.assertEqual(entry.item.artists, ("First", "Second"))
        self.assertEqual(entry.item.name, "Song (Live)")
        self.assertEqual(entry.item.isrc, "US1234567890")
        self.assertEqual(entry.item.duration_ms, 180000)
        self.assertIs(entry.item.explicit, False)
        self.assertIsNone(entry.item.is_playable)
        self.assertEqual(entry.status, "supported")

    def test_missing_and_unsupported_entries_keep_positions(self):
        rows = [None, {"item": None}, {"item": track(id=None), "is_local": True},
                {"item": track(type="episode")}, {"item": track(type="future")},
                {"item": track(is_playable=False)}, {"item": track(id=None)}]
        self.client.playlist_items.return_value = page(rows)
        entries = self.read().entries
        self.assertEqual([e.position for e in entries], list(range(len(rows))))
        self.assertEqual([e.reason for e in entries],
                         ["missing_item", "missing_item", "local_file", "episode",
                          "unknown_type", "not_playable", "missing_catalog_id"])

    def test_current_and_legacy_item_fields(self):
        self.assertEqual(normalize_entry({"track": track()}, 0).status, "supported")
        self.assertIsNone(normalize_entry({"item": None, "track": track()}, 0).item)
        entry = normalize_entry({"item": track(linked_from={"id": "original"},
                                               restrictions={"reason": "market"})}, 0)
        self.assertEqual(entry.item.original_id, "original")
        self.assertEqual(entry.reason, "market")
        self.assertEqual(entry.status, "unavailable")

    def test_empty_playlist_is_a_valid_snapshot(self):
        self.client.playlist_items.return_value = page([])
        self.assertEqual(self.read().entries, ())
        self.assertEqual(self.client.playlist.call_count, 2)

    def test_fingerprint_excludes_capture_time_and_changes_with_order(self):
        self.client.playlist_items.return_value = page([{"item": track(id="one")}, {"item": track(id="two")}])
        first, second = self.read(), self.read()
        self.assertEqual(first.fingerprint, second.fingerprint)
        self.assertEqual(len(first.fingerprint), 64)
        self.client.playlist_items.return_value = page([{"item": track(id="two")}, {"item": track(id="one")}])
        self.assertNotEqual(first.fingerprint, self.read().fingerprint)

    def test_version_or_metadata_changes_abort(self):
        for changed in (metadata(snapshot_id="version-two"), metadata(name="Renamed")):
            with self.subTest(changed=changed):
                self.client.playlist.side_effect = [metadata(), changed]
                self.client.playlist_items.return_value = page([{"item": track()}])
                with self.assertRaisesRegex(PlaylistReadError, "changed while reading"):
                    self.read()

    def test_missing_snapshot_version_is_not_claimed_consistent(self):
        self.client.playlist.return_value = metadata(snapshot_id=None)
        with self.assertRaisesRegex(PlaylistReadError, "usable playlist version"):
            self.read()
        self.client.playlist_items.assert_not_called()

    def test_total_changes_and_truncated_pages_abort(self):
        for pages in ([page([{"item": track()}], total=2)],
                      [page([{"item": track()}], total=2, next_offset=1),
                       page([{"item": track()}], offset=1, total=3)]):
            with self.subTest(pages=pages):
                self.client.playlist_items.side_effect = pages
                with self.assertRaises(PlaylistReadError):
                    self.read()

    def test_skipped_repeated_foreign_or_empty_next_page_is_rejected(self):
        for next_url in (
            f"https://api.spotify.com/v1/playlists/{PLAYLIST_ID}/items?offset=0",
            f"https://api.spotify.com/v1/playlists/{PLAYLIST_ID}/items?offset=2",
            "https://evil.example/items?offset=1",
        ):
            with self.subTest(next_url=next_url):
                self.client.playlist_items.return_value = {
                    **page([{"item": track()}], total=3), "next": next_url}
                with self.assertRaisesRegex(PlaylistReadError, "pagination"):
                    self.read()
        self.client.playlist_items.return_value = page([], total=3, next_offset=1)
        with self.assertRaisesRegex(PlaylistReadError, "pagination"):
            self.read()

    def test_malformed_rows_and_page_offsets_are_not_silently_skipped(self):
        for malformed in (page([{}]), page([{"item": "bad"}]), page([], offset=50),
                          {"items": [], "next": None}):
            with self.subTest(malformed=malformed):
                self.client.playlist_items.return_value = malformed
                with self.assertRaises(PlaylistReadError):
                    self.read()

    def test_access_errors_are_actionable_and_sanitized(self):
        for status in (403, 404):
            with self.subTest(status=status):
                self.client.playlist_items.side_effect = SpotifyException(status, -1, "secret")
                with self.assertRaisesRegex(PlaylistReadError, "content access") as error:
                    self.read()
                self.assertNotIn("secret", str(error.exception))

    def test_invalid_id_fails_before_network(self):
        with self.assertRaisesRegex(PlaylistReadError, "22-character"):
            read_playlist_snapshot(self.client, "../bad", user_id="me")
        self.client.playlist.assert_not_called()


class SnapshotCommandTests(TestCase):
    def test_dispatch_and_argument_validation(self):
        settings = Conf("fake", "http://127.0.0.1:8000/callback")
        with patch("tune_sync.app.load_conf", return_value=settings), \
             patch("tune_sync.app.spotify_command", return_value=0) as command:
            self.assertEqual(main(["spotify", "playlist", PLAYLIST_ID, "--json"]), 0)
        command.assert_called_once_with(settings, "playlist", None, json_output=True, playlist_id=PLAYLIST_ID)
        for arguments in (["spotify", "playlist"], ["spotify", "playlist", "invalid"],
                          ["spotify", "status", PLAYLIST_ID]):
            with self.subTest(arguments=arguments), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exit:
                main(arguments)
            self.assertEqual(exit.exception.code, 2)

    def test_json_output_and_failure_never_emit_partial_snapshot(self):
        client = Mock()
        client.playlist.return_value = metadata()
        client.playlist_items.return_value = page([{"item": track()}])
        @contextmanager
        def connection(*args, **kwargs):
            yield client, {"id": "me", "country": "US"}
        settings = Conf("fake", "http://127.0.0.1:8000/callback")
        with patch("tune_sync.auth.connected_spotify", connection):
            with redirect_stdout(io.StringIO()) as output:
                self.assertEqual(spotify_command(settings, "playlist", None,
                                                playlist_id=PLAYLIST_ID, json_output=True), 0)
            data = json.loads(output.getvalue())
            self.assertEqual(data["entries"][0]["position"], 0)
            self.assertEqual(data["playlist"]["item_count"], 1)
            client.playlist.side_effect = [metadata(), metadata(snapshot_id="changed")]
            with redirect_stdout(io.StringIO()) as output, redirect_stderr(io.StringIO()) as errors:
                self.assertEqual(spotify_command(settings, "playlist", None,
                                                playlist_id=PLAYLIST_ID, json_output=True), 1)
            self.assertEqual(output.getvalue(), "")
            self.assertIn("changed", errors.getvalue())

"""Offline auth tests: fake Spotify responses and a real loopback callback."""

import contextlib
import io
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit
from urllib.request import urlopen
from urllib.error import HTTPError

from spotipy.cache_handler import MemoryCacheHandler
from spotipy.oauth2 import SpotifyPKCE
from spotipy.exceptions import SpotifyOauthError, SpotifyException

from tune_sync.auth import (ConnectionError, ConnectionStore, connection_action,
                            wait_for_callback)
from tune_sync.conf import Conf
from tune_sync.app import spotify_command


def token(expires_at=None):
    return {"access_token": "fake-access", "refresh_token": "fake-refresh",
            "scope": "user-read-private", "expires_at": expires_at or time.time() + 3600}


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = ConnectionStore(self.directory.name, "fake-client")
        self.settings = Conf("fake-client", "http://127.0.0.1:8000/callback")
        self.account = {"id": "one", "account_id": "stable-one", "display_name": "Example", "country": "US"}

    def save(self, credentials=None):
        with self.store.locked():
            self.store.save(credentials or token(), self.account)

    def test_no_connection_never_constructs_auth_or_opens_browser(self):
        with patch("tune_sync.auth.SpotifyPKCE", side_effect=AssertionError("auth")):
            self.assertIsNone(connection_action(self.settings, self.store, "status"))

    def test_store_permissions_and_client_isolation(self):
        self.save()
        self.assertEqual(self.store.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.store.directory.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.store.read()["account"], self.account)
        self.assertIsNone(ConnectionStore(self.directory.name, "other-client").read())

    def test_invalid_store_and_disconnect(self):
        self.save()
        self.store.path.write_text('[]')
        with self.assertRaises(ConnectionError):
            self.store.read()
        self.assertIsNone(connection_action(self.settings, self.store, "disconnect"))
        self.assertFalse(self.store.path.exists())
        connection_action(self.settings, self.store, "disconnect")

    def test_parallel_commands_fail_without_overwriting(self):
        with self.store.locked():
            with self.assertRaisesRegex(ConnectionError, "Another"):
                with self.store.locked():
                    self.fail("Acquired competing lock")

    def test_status_reuses_token_without_browser(self):
        self.save()
        with patch("tune_sync.auth.create_spotify_client") as client:
            client.return_value.current_user.return_value = self.account
            result = connection_action(self.settings, self.store, "status",
                                       callback=Mock(side_effect=AssertionError("browser")))
        self.assertEqual(result, self.account)
        client.assert_called_once_with("fake-access")

    def test_expired_token_refresh_is_persisted_and_retains_refresh_token(self):
        self.save(token(expires_at=1))
        response = Mock()
        response.json.return_value = {"access_token": "new-access", "expires_in": 3600,
                                      "scope": "user-read-private"}
        with patch("requests.sessions.Session.post", return_value=response) as post, \
             patch("tune_sync.auth.create_spotify_client") as client:
            client.return_value.current_user.return_value = self.account
            connection_action(self.settings, self.store, "status")
        self.assertEqual(post.call_args.kwargs["data"]["grant_type"], "refresh_token")
        self.assertNotIn("client_secret", post.call_args.kwargs["data"])
        self.assertEqual(self.store.read()["token"]["access_token"], "new-access")
        self.assertEqual(self.store.read()["token"]["refresh_token"], "fake-refresh")

    def test_connect_exchanges_code_with_pkce_and_verifies_account(self):
        response = Mock()
        response.json.return_value = {**token(), "expires_in": 3600}
        def callback(auth):
            parameters = parse_qs(urlsplit(auth.get_authorize_url()).query)
            self.assertEqual(parameters["code_challenge_method"], ["S256"])
            self.assertTrue(parameters["state"][0])
            self.assertEqual(parameters["scope"], ["user-read-private"])
            return "fake-code"
        with patch("requests.sessions.Session.post", return_value=response) as post, \
             patch("tune_sync.auth.create_spotify_client") as client:
            client.return_value.current_user.return_value = self.account
            connection_action(self.settings, self.store, "connect", callback=callback)
        payload = post.call_args.kwargs["data"]
        self.assertEqual(payload["code"], "fake-code")
        self.assertTrue(payload["code_verifier"])
        self.assertNotIn("client_secret", payload)
        self.assertEqual(self.store.read()["account"], self.account)

    def test_failed_reconnect_preserves_previous_connection(self):
        self.save()
        before = self.store.path.read_bytes()
        with self.assertRaises(ConnectionError):
            connection_action(self.settings, self.store, "connect",
                              callback=Mock(side_effect=ConnectionError("Denied")))
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_failed_account_verification_does_not_replace_saved_connection(self):
        self.save()
        before = self.store.path.read_bytes()
        response = Mock()
        response.json.return_value = {**token(), "expires_in": 3600}
        with patch("requests.sessions.Session.post", return_value=response), \
             patch("tune_sync.auth.create_spotify_client") as client:
            client.return_value.current_user.side_effect = SpotifyException(403, -1, "denied")
            with self.assertRaises(SpotifyException):
                connection_action(self.settings, self.store, "connect", callback=lambda auth: "code")
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_failed_atomic_save_preserves_record_and_cleans_temporary_file(self):
        self.save()
        before = self.store.path.read_bytes()
        with self.store.locked(), patch("tune_sync.auth.os.replace", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                self.store.save(token(), {"id": "new"})
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertEqual(list(self.store.directory.glob(".spotify-*")), [])

    def test_different_account_during_status_is_not_saved(self):
        self.save()
        with patch("tune_sync.auth.create_spotify_client") as client:
            client.return_value.current_user.return_value = {"id": "other", "account_id": "other"}
            with self.assertRaisesRegex(ConnectionError, "account changed"):
                connection_action(self.settings, self.store, "status")
        self.assertEqual(self.store.read()["account"], self.account)

    def test_401_refreshes_once(self):
        self.save()
        response = Mock()
        response.json.return_value = {**token(), "access_token": "new", "expires_in": 3600}
        with patch("requests.sessions.Session.post", return_value=response) as post, \
             patch("tune_sync.auth.create_spotify_client") as client:
            client.return_value.current_user.side_effect = [SpotifyException(401, -1, "expired"), self.account]
            connection_action(self.settings, self.store, "status")
        self.assertEqual(post.call_count, 1)
        self.assertEqual(self.store.read()["token"]["access_token"], "new")

    def test_cli_sanitizes_auth_and_api_failures(self):
        for error, expected in (
            (SpotifyOauthError("secret-in-error", error="invalid_grant"), "revoked"),
            (SpotifyException(403, -1, "secret-in-error"), "allowlist"),
            (SpotifyException(429, -1, "secret-in-error"), "quota"),
        ):
            with self.subTest(error=type(error)), \
                 patch("tune_sync.auth.connection_action", side_effect=error), \
                 contextlib.redirect_stderr(io.StringIO()) as output:
                self.assertEqual(spotify_command(self.settings, "status", Path(self.directory.name)), 1)
                self.assertIn(expected, output.getvalue())
                self.assertNotIn("secret-in-error", output.getvalue())


class CallbackTests(unittest.TestCase):
    def make_auth(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        return SpotifyPKCE(client_id="fake", redirect_uri=f"http://127.0.0.1:{port}/callback",
                           state="expected-state", cache_handler=MemoryCacheHandler())

    def run_requests(self, auth, paths):
        statuses = []
        errors = []
        def requests():
            try:
                for path in paths:
                    try:
                        with urlopen(auth.redirect_uri.replace('/callback', path), timeout=2) as response:
                            statuses.append(response.status)
                    except HTTPError as error:
                        statuses.append(error.code)
            except Exception as error:
                errors.append(error)
        thread = threading.Thread(target=requests, daemon=True)
        def browser(url):
            thread.start()
            return True
        return statuses, errors, thread, browser

    def test_wrong_path_and_state_are_ignored_until_valid_callback(self):
        auth = self.make_auth()
        paths = ["/wrong?state=expected-state&code=bad", "/callback?state=wrong&code=bad",
                 "/callback?state=expected-state&state=other&code=bad",
                 "/callback?state=expected-state&code=good"]
        statuses, errors, thread, browser = self.run_requests(auth, paths)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(wait_for_callback(auth, timeout=3, open_browser=browser), "good")
        thread.join(2)
        self.assertFalse(errors)
        self.assertEqual(statuses, [404, 400, 400, 200])

    def test_denial_returns_actionable_error(self):
        auth = self.make_auth()
        _, errors, thread, browser = self.run_requests(auth, ["/callback?state=expected-state&error=access_denied"])
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(ConnectionError, "denied"):
            wait_for_callback(auth, timeout=3, open_browser=browser)
        thread.join(2)
        self.assertFalse(errors)

    def test_timeout_closes_listener(self):
        auth = self.make_auth()
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(ConnectionError, "timed out"):
            wait_for_callback(auth, timeout=0.01, open_browser=lambda url: False)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", urlsplit(auth.redirect_uri).port))

    def test_busy_port_does_not_open_browser(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            auth = self.make_auth()
            auth.redirect_uri = f"http://127.0.0.1:{listener.getsockname()[1]}/callback"
            with self.assertRaisesRegex(ConnectionError, "callback port"):
                wait_for_callback(auth, open_browser=Mock(side_effect=AssertionError("browser")))

    def test_unsupported_callback_does_not_open_browser(self):
        auth = self.make_auth()
        auth.redirect_uri = "https://example.com/callback"
        with self.assertRaisesRegex(ConnectionError, "loopback"):
            wait_for_callback(auth, open_browser=Mock(side_effect=AssertionError("browser")))

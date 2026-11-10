"""Apple checks use temporary signing keys and fake HTTP responses only."""

from contextlib import redirect_stdout, redirect_stderr
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
import jwt
import requests

from tune_sync.apple import AppleAPIError, generate_developer_token, get_catalog_song
from tune_sync.app import main
from tune_sync.conf import AppleConf, ConfigurationError, load_apple_conf


class AppleSettingsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.pem = self.key.private_bytes(serialization.Encoding.PEM,
                                         serialization.PrivateFormat.PKCS8,
                                         serialization.NoEncryption())
        self.key_path = self.root / "test.p8"
        self.key_path.write_bytes(self.pem)
        self.env_file = self.root / "settings.env"
        self.env_file.write_text('APPLE_TEAM_ID=ABCDEFGHIJ\nAPPLE_KEY_ID=1234567890\n'
                                 'APPLE_PRIVATE_KEY_PATH=test.p8\nAPPLE_STOREFRONT=GB\n')
        env = patch.dict(os.environ, {}, clear=True)
        env.start()
        self.addCleanup(env.stop)

    def test_apple_only_settings_and_relative_key_path(self):
        settings = load_apple_conf(self.env_file)
        self.assertEqual(settings.private_key_path, self.key_path.resolve())
        self.assertEqual(settings.storefront, "gb")
        self.assertNotIn("ABCDEFGHIJ", repr(settings))

    def test_missing_and_blank_overridden_settings(self):
        os.environ["APPLE_KEY_ID"] = ""
        with self.assertRaisesRegex(ConfigurationError, "APPLE_KEY_ID"):
            load_apple_conf(self.env_file)
        with self.assertRaises(ConfigurationError) as error:
            load_apple_conf(self.root / "absent")
        self.assertIn("APPLE_TEAM_ID", str(error.exception))
        self.assertIn("APPLE_PRIVATE_KEY_PATH", str(error.exception))

    def test_storefront_defaults_and_rejects_invalid_input(self):
        self.env_file.write_text(self.env_file.read_text().replace('APPLE_STOREFRONT=GB\n', ''))
        self.assertEqual(load_apple_conf(self.env_file).storefront, "us")
        for value in ("", "../../bad", "usa"):
            with self.subTest(value=value), patch.dict(os.environ, {"APPLE_STOREFRONT": value}):
                with self.assertRaisesRegex(ConfigurationError, "APPLE_STOREFRONT"):
                    load_apple_conf(self.env_file)

    def test_signed_token_verifies_with_public_key_and_correct_claims(self):
        settings = load_apple_conf(self.env_file)
        now = int(time.time())
        with patch("tune_sync.apple.time.time", return_value=now):
            signed = generate_developer_token(settings)
        claims = jwt.decode(signed, self.key.public_key(), algorithms=["ES256"], issuer=settings.team_id)
        self.assertEqual(claims, {"iss": settings.team_id, "iat": now, "exp": now + 3600})
        self.assertEqual(jwt.get_unverified_header(signed)["kid"], settings.key_id)
        with self.assertRaises(jwt.InvalidSignatureError):
            jwt.decode(signed, ec.generate_private_key(ec.SECP256R1()).public_key(), algorithms=["ES256"])
        self.assertEqual(self.key_path.read_bytes(), self.pem)

    def test_invalid_missing_encrypted_and_wrong_curve_keys_fail_safely(self):
        settings = load_apple_conf(self.env_file)
        with self.assertRaisesRegex(ConfigurationError, "Cannot read"):
            generate_developer_token(replace(settings, private_key_path=self.root / "missing.p8"))
        encrypted = self.key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.BestAvailableEncryption(b"test-password"))
        wrong_curve = ec.generate_private_key(ec.SECP384R1()).private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        for contents in (b"secret-invalid-content", encrypted, wrong_curve):
            with self.subTest(kind=contents[:10]):
                self.key_path.write_bytes(contents)
                with self.assertRaises(ConfigurationError) as error:
                    generate_developer_token(settings)
                self.assertNotIn("secret-invalid-content", str(error.exception))
                self.assertNotIn("BEGIN PRIVATE KEY", str(error.exception))

    def test_local_check_needs_no_spotify_and_makes_no_request(self):
        with patch("requests.sessions.Session.request", side_effect=AssertionError("Network")), \
             redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["--env-file", str(self.env_file), "apple", "check"]), 0)
        self.assertIn("No Apple API request", output.getvalue())
        self.assertNotIn("eyJ", output.getvalue())

    def test_song_command_json_and_storefront_override(self):
        session = Mock()
        session.get.return_value.status_code = 200
        session.get.return_value.json.return_value = song_response()
        with patch("requests.Session") as factory, redirect_stdout(io.StringIO()) as output:
            factory.return_value.__enter__.return_value = session
            self.assertEqual(main(["--env-file", str(self.env_file), "apple", "song", "123",
                                   "--storefront", "us", "--json"]), 0)
        body = json.loads(output.getvalue())
        self.assertEqual(body["song"]["storefront"], "us")
        self.assertNotIn("Authorization", output.getvalue())
        self.assertNotIn("eyJ", output.getvalue())
        self.assertTrue(session.get.call_args.args[0].endswith('/us/songs/123'))

    def test_command_failure_has_no_partial_output_or_sensitive_details(self):
        with patch("tune_sync.apple.get_catalog_song", side_effect=AppleAPIError("Apple denied catalog access.")), \
             redirect_stdout(io.StringIO()) as output, redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(main(["--env-file", str(self.env_file), "apple", "song", "123", "--json"]), 1)
        self.assertEqual(output.getvalue(), "")
        self.assertIn("denied", errors.getvalue())
        self.assertNotIn("eyJ", errors.getvalue())


def song_response(attributes=None):
    return {"data": [{"id": "123", "type": "songs", "attributes": attributes if attributes is not None else
                      {"name": "Song", "artistName": "Artist", "albumName": "Album", "isrc": "US1234567890",
                       "durationInMillis": 120000, "contentRating": "explicit", "url": "https://music.apple.com/song/123"}}]}


class AppleCatalogTests(unittest.TestCase):
    def setUp(self):
        self.session = Mock()
        self.response = self.session.get.return_value
        self.response.status_code = 200
        self.response.json.return_value = song_response()

    def read(self, song_id="123", storefront="us"):
        return get_catalog_song("sensitive-token", song_id, storefront=storefront, session=self.session)

    def test_request_and_normalized_response(self):
        song = self.read()
        self.session.get.assert_called_once_with(
            "https://api.music.apple.com/v1/catalog/us/songs/123",
            headers={"Authorization": "Bearer sensitive-token", "Accept": "application/json"},
            timeout=15, allow_redirects=False)
        self.assertEqual(song.artist_name, "Artist")
        self.assertEqual(song.duration_ms, 120000)
        self.assertEqual(song.content_rating, "explicit")
        self.assertEqual(song.isrc, "US1234567890")

    def test_missing_optional_fields_are_unknown(self):
        self.response.json.return_value = song_response({})
        song = self.read()
        self.assertIsNone(song.name)
        self.assertIsNone(song.duration_ms)
        self.assertIsNone(song.content_rating)

    def test_errors_are_sanitized_and_not_retried(self):
        for status, phrase in ((400, "catalog request"), (401, "developer token"), (403, "denied"),
                               (404, "not found"), (429, "rate-limiting"), (500, "later"), (302, "later")):
            with self.subTest(status=status):
                self.session.reset_mock()
                self.response.status_code = status
                with self.assertRaisesRegex(AppleAPIError, phrase) as error:
                    self.read()
                self.assertNotIn("sensitive-token", str(error.exception))
                self.assertEqual(self.session.get.call_count, 1)
                self.response.json.assert_not_called()

    def test_invalid_ids_and_storefronts_do_not_reach_network(self):
        for song_id, storefront in (("../123", "us"), ("i.library", "us"), ("123", "../us")):
            with self.subTest(song_id=song_id, storefront=storefront), self.assertRaises(ConfigurationError):
                self.read(song_id, storefront)
        self.session.get.assert_not_called()

    def test_timeout_is_actionable(self):
        self.session.get.side_effect = requests.exceptions.Timeout("sensitive-token")
        with self.assertRaisesRegex(AppleAPIError, "Could not reach") as error:
            self.read()
        self.assertNotIn("sensitive-token", str(error.exception))

    def test_malformed_empty_and_wrong_resource_responses(self):
        for body in ({}, {"data": []}, {"data": [None]},
                     {"data": [{"id": "123", "type": "albums", "attributes": {}}]},
                     {"data": [{"id": "456", "type": "songs", "attributes": {}}]}):
            with self.subTest(body=body):
                self.response.json.return_value = body
                with self.assertRaises(AppleAPIError):
                    self.read()
        self.response.json.side_effect = ValueError("sensitive-token")
        with self.assertRaisesRegex(AppleAPIError, "invalid JSON"):
            self.read()

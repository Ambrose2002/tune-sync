"""Explicit Spotify PKCE connection, bounded callback, and local credential storage."""

from contextlib import contextmanager
import fcntl
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import secrets
import socket
import sys
import tempfile
import time
from urllib.parse import parse_qs, urlsplit
import webbrowser

from spotipy.cache_handler import MemoryCacheHandler
from spotipy.oauth2 import SpotifyPKCE
from spotipy.exceptions import SpotifyException

from .spotify import create_spotify_client

# This checkpoint only identifies the account. Library/write scopes come later.
SCOPE = "user-read-private"


class ConnectionError(ValueError):
    """An actionable, credential-free connection error."""


def default_data_dir():
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "tune-sync"
    return Path.home() / ".local" / "share" / "tune-sync"


class ConnectionStore:
    def __init__(self, directory, client_id):
        self.directory = Path(directory).expanduser() / "spotify"
        self.client_id = client_id
        key = hashlib.sha256(client_id.encode()).hexdigest()
        self.path = self.directory / f"spotify-{key}.json"

    @contextmanager
    def locked(self):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        # The directory is dedicated to Tune Sync; don't use a shared directory.
        self.directory.chmod(0o700)
        descriptor = os.open(str(self.path) + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(descriptor, "w") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ConnectionError("Another Spotify connection command is running. Try again when it finishes.") from None
            yield

    def read(self):
        try:
            with self.path.open() as handle:
                record = json.load(handle)
        except FileNotFoundError:
            return None
        except (ValueError, UnicodeError):
            raise ConnectionError("Saved connection is unreadable. Run spotify connect to replace it.") from None
        try:
            token = record["token"]
            valid = (
                record["client_id"] == self.client_id
                and isinstance(record["account"], dict)
                and isinstance(token["access_token"], str) and bool(token["access_token"])
                and isinstance(token["refresh_token"], str) and bool(token["refresh_token"])
                and isinstance(token["expires_at"], (int, float))
                and isinstance(token["scope"], str)
            )
        except (KeyError, TypeError):
            valid = False
        if not valid:
            raise ConnectionError("Saved connection is invalid. Run spotify connect to replace it.")
        return record

    def save(self, token, account):
        descriptor, temporary = tempfile.mkstemp(prefix=".spotify-", dir=self.directory)
        try:
            with os.fdopen(descriptor, "w") as handle:
                json.dump({"client_id": self.client_id, "token": token, "account": account}, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def delete(self):
        self.path.unlink(missing_ok=True)


def wait_for_callback(auth, *, timeout=180, open_browser=webbrowser.open):
    """Accept only the configured loopback path and this attempt's OAuth state."""
    redirect = urlsplit(auth.redirect_uri)
    if (redirect.scheme != "http" or redirect.hostname not in ("127.0.0.1", "::1")
            or not redirect.port or redirect.query):
        raise ConnectionError(
            "Local login requires an HTTP loopback callback with an explicit port and no query, "
            "such as http://127.0.0.1:8000/callback. Register that exact URI in Spotify."
        )
    outcome = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Callback URLs contain authorization codes.

        def do_GET(self):
            request = urlsplit(self.path)
            query = parse_qs(request.query, keep_blank_values=True)
            state = query.get("state", [])
            if request.path != (redirect.path or "/"):
                status, message = 404, "Unknown callback path."
            elif len(state) != 1 or not hmac.compare_digest(state[0].encode(), auth.state.encode()):
                status, message = 400, "Invalid login state. Return to the original login tab."
            elif query.get("error"):
                outcome["error"] = "Spotify authorization was denied. Run spotify connect to try again."
                status, message = 400, "Authorization was not completed. Return to Tune Sync."
            elif len(query.get("code", [])) != 1 or not query["code"][0]:
                status, message = 400, "Missing authorization code."
            else:
                outcome["code"] = query["code"][0]
                status, message = 200, "Authorization received. Return to Tune Sync to check the connection result."
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(message.encode())

    class Server(HTTPServer):
        address_family = socket.AF_INET6 if redirect.hostname == "::1" else socket.AF_INET

        def get_request(self):
            connection, address = super().get_request()
            connection.settimeout(2)
            return connection, address

        def handle_error(self, request, address):
            pass  # A closed browser connection must not emit a callback traceback.

    try:
        server = Server((redirect.hostname, redirect.port), Handler)
    except OSError:
        raise ConnectionError("Cannot open the callback port. Close the app using it or configure another registered port.") from None
    with server:
        url = auth.get_authorize_url() + "&show_dialog=true"
        print("Opening Spotify login. If the browser does not open, visit:\n" + url, flush=True)
        try:
            open_browser(url)
        except webbrowser.Error:
            pass  # The URL above provides a manual fallback.
        deadline = time.monotonic() + timeout
        while not outcome and time.monotonic() < deadline:
            server.timeout = min(0.5, max(0, deadline - time.monotonic()))
            server.handle_request()
    if "error" in outcome:
        raise ConnectionError(outcome["error"])
    if "code" not in outcome:
        raise ConnectionError("Spotify login timed out. Run spotify connect to try again.")
    return outcome["code"]


def account_summary(profile):
    if not isinstance(profile, dict) or not profile.get("id"):
        raise ConnectionError("Spotify returned an incomplete account profile. Try again.")
    return {key: profile.get(key) for key in ("id", "account_id", "display_name", "country")}


def connection_action(settings, store, action, *, callback=wait_for_callback):
    """Perform one explicit action. Never fall back to interactive SDK login."""
    with store.locked():
        if action == "disconnect":
            store.delete()
            return None
        record = store.read() if action == "status" else None
        if action == "status" and record is None:
            return None
        memory = MemoryCacheHandler(record["token"] if record else None)
        auth = SpotifyPKCE(
            client_id=settings.spotify_client_id, redirect_uri=settings.spotify_callback_url,
            scope=SCOPE, state=secrets.token_urlsafe(32), cache_handler=memory,
            open_browser=False, requests_timeout=15,
        )
        if action == "connect":
            code = callback(auth)
            if not code:
                raise ConnectionError("No authorization code received. Run spotify connect again.")
            auth.get_access_token(code=code, check_cache=False)
            token = memory.get_cached_token()
        else:
            token = auth.validate_token(record["token"])
        if not token or not set(SCOPE.split()).issubset(token.get("scope", "").split()):
            raise ConnectionError("Spotify permissions are missing. Run spotify connect again.")
        try:
            profile = create_spotify_client(token["access_token"]).current_user()
        except SpotifyException as error:
            if error.http_status != 401:
                raise
            token = auth.refresh_access_token(token["refresh_token"])
            profile = create_spotify_client(token["access_token"]).current_user()
        account = account_summary(profile)
        if record:
            previous = record["account"]
            identity = "account_id" if previous.get("account_id") and account.get("account_id") else "id"
            if account.get(identity) != previous.get(identity):
                raise ConnectionError("The connected account changed. Run spotify connect to select it explicitly.")
        store.save(token, account)
        return account

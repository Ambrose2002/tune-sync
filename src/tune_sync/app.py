"""Local configuration checks and explicit Spotify connection commands."""

import argparse
from dataclasses import asdict
import json
import logging
from pathlib import Path
import sys

from .conf import ConfigurationError, load_conf


def main(argv=None):
    parser = argparse.ArgumentParser(description="Tune Sync configuration and account connections.")
    parser.add_argument("--env-file", type=Path, help="Path to an existing .env file")
    commands = parser.add_subparsers(dest="provider")
    spotify = commands.add_parser("spotify", help="Connect or inspect a Spotify account")
    spotify.add_argument("action", choices=("connect", "status", "disconnect", "playlists", "playlist"))
    spotify.add_argument("playlist_id", nargs="?", help="Playlist ID for the playlist command")
    spotify.add_argument("--data-dir", type=Path, help="Application data directory override")
    spotify.add_argument("--json", action="store_true", help="Emit playlist listings or snapshots as JSON")
    args = parser.parse_args(argv)
    if args.provider == "spotify" and args.json and args.action not in ("playlists", "playlist"):
        parser.error("--json is only available for spotify playlists or spotify playlist")
    if args.provider == "spotify":
        if args.action == "playlist":
            from .snapshots import validate_playlist_id
            from .playlists import PlaylistReadError
            try:
                validate_playlist_id(args.playlist_id)
            except PlaylistReadError as error:
                parser.error(str(error))
        elif args.playlist_id is not None:
            parser.error("A playlist ID is only accepted by spotify playlist")
    if args.env_file is not None and not args.env_file.is_file():
        print("Configuration error: --env-file must point to an existing file.", file=sys.stderr)
        return 1
    try:
        settings = load_conf(args.env_file)
    except (ConfigurationError, OSError) as error:
        message = str(error) if isinstance(error, ConfigurationError) else "Cannot read configuration file."
        print(f"Configuration error:\n{message}", file=sys.stderr)
        return 1
    if args.provider is None:
        print("Configuration is valid. No login or Spotify API requests were made.")
        return 0
    if args.action == "playlist":
        return spotify_command(settings, args.action, args.data_dir,
                               json_output=args.json, playlist_id=args.playlist_id)
    return spotify_command(settings, args.action, args.data_dir, json_output=args.json)


def spotify_command(settings, action, data_dir, *, json_output=False, playlist_id=None):
    from requests.exceptions import RequestException
    from spotipy.exceptions import SpotifyException, SpotifyOauthError
    from .auth import (ConnectionError, ConnectionStore, connected_spotify,
                       connection_action, default_data_dir)
    from .playlists import PlaylistReadError, list_playlists

    # The SDK's debug logs include token request bodies. CLI output is deliberately sanitized.
    loggers = [logging.getLogger(name) for name in ("spotipy.oauth2", "spotipy.client")]
    previous_levels = [logger.level for logger in loggers]
    for logger in loggers:
        logger.setLevel(logging.CRITICAL)
    try:
        store = ConnectionStore(data_dir or default_data_dir(), settings.spotify_client_id)
        if action == "playlist":
            from .snapshots import read_playlist_snapshot
            with connected_spotify(settings, store) as (client, account):
                snapshot = read_playlist_snapshot(client, playlist_id, user_id=account["id"],
                                                  source_country=account.get("country"))
            print_snapshot(snapshot, account, json_output=json_output)
            return 0
        if action == "playlists":
            with connected_spotify(settings, store) as (client, account):
                playlists = list_playlists(client, user_id=account["id"])
            print_playlists(playlists, account, json_output=json_output)
            return 0
        account = connection_action(settings, store, action)
        if action == "disconnect":
            print("Local Spotify connection removed. Spotify's app authorization and the old .spotify_cache are unchanged.")
        elif account is None:
            print("Spotify is not connected. Run: tune-sync spotify connect")
        else:
            # JSON escaping prevents untrusted profile text from emitting terminal controls.
            name = json.dumps(account.get("display_name") or account["id"], ensure_ascii=True)
            identifier = json.dumps(account.get("account_id") or account["id"], ensure_ascii=True)
            print(f"Connected to Spotify as {name}. Account: {identifier}")
        return 0
    except (ConnectionError, PlaylistReadError) as error:
        message = str(error)
    except SpotifyOauthError as error:
        if error.error == "invalid_grant":
            message = "Spotify authorization expired or was revoked. Run spotify connect again."
        else:
            message = "Spotify could not authorize this connection. Check the client ID and registered callback, then reconnect."
    except SpotifyException as error:
        message = {
            401: "Spotify authorization is no longer valid. Run spotify connect again.",
            403: "Spotify denied access. Check the app's user allowlist, owner Premium subscription, and granted permissions.",
            429: "Spotify is rate-limiting this app or its quota is exhausted. Try again later.",
        }.get(error.http_status, "Spotify could not complete the request. Try again later.")
    except RequestException:
        message = "Could not reach Spotify. Check your connection and try again."
    except OSError:
        message = "Could not access the local connection store. Check the application data directory permissions."
    except KeyboardInterrupt:
        print("Spotify connection cancelled.", file=sys.stderr)
        return 130
    finally:
        for logger, level in zip(loggers, previous_levels):
            logger.setLevel(level)
    print(f"Connection error: {message}", file=sys.stderr)
    return 1


def print_playlists(playlists, account, *, json_output=False):
    """Render only a complete result; keep JSON stdout free of status messages."""
    if json_output:
        print(json.dumps({"provider": "spotify", "account": account,
                          "count": len(playlists),
                          "playlists": [asdict(playlist) for playlist in playlists]}, indent=2))
        return
    print(f"{len(playlists)} Spotify playlist(s). Contents access has not been checked.")
    for playlist in playlists:
        ownership = "owned" if playlist.is_owned else "other owner" if playlist.is_owned is False else "owner unknown"
        count = str(playlist.item_count) if playlist.item_count is not None else "unknown"
        print(f"{json.dumps(playlist.name)} | ID: {json.dumps(playlist.id)} | {ownership} | items: {count}")
        print(f"  Owner: {json.dumps(playlist.owner_name or playlist.owner_id)} | URL: {json.dumps(playlist.url)}")


def print_snapshot(snapshot, account, *, json_output=False):
    if json_output:
        print(json.dumps({"account": account, **asdict(snapshot)}, indent=2))
        return
    from collections import Counter
    counts = Counter(entry.status for entry in snapshot.entries)
    print(f"{json.dumps(snapshot.playlist.name)}: {len(snapshot.entries)} entries; "
          f"{counts['supported']} supported, {counts['unavailable']} unavailable, "
          f"{counts['unsupported']} unsupported.")
    print(f"Version: {json.dumps(snapshot.snapshot_id)} | Captured: {snapshot.captured_at}")
    for entry in snapshot.entries:
        name = entry.item.name if entry.item else None
        artists = ", ".join(entry.item.artists) if entry.item else ""
        reason = f" ({json.dumps(entry.reason)})" if entry.reason else ""
        print(f"{entry.position + 1}. {json.dumps(name)} — {json.dumps(artists)} "
              f"[{entry.status}]{reason}")

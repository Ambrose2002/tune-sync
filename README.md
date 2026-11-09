# Tune Sync

A tool for transferring playlists between music streaming services.

## Current checkpoint: complete Spotify playlist snapshots

Tune Sync validates settings, connects a Spotify account using Authorization
Code with PKCE, lists playlists, and reads ordered playlist snapshots.
Cross-service transfers are not implemented yet.
The CLI connection workflow currently supports macOS and Linux.

### Setup

Use the existing `myenv`, or create one, then install the project:

```sh
python3 -m venv myenv
myenv/bin/python -m pip install -e .
```

If `.env` does not exist, copy `.env.example` to `.env`. Set:

- `SPOTIFY_CLIENT_ID`: your Spotify developer application's client ID.
- `SPOTIFY_CALLBACK_URL`: for example `http://127.0.0.1:8000/callback`.
  Register that exact redirect URI in the Spotify developer dashboard.

PKCE does not require a client secret. An existing `SPOTIFY_CLIENT_SECRET` is
ignored; Tune Sync does not edit your `.env` or automatically remove it.

For local login, use HTTP with `127.0.0.1` or `[::1]`, an explicit nonzero port,
and no query string. `localhost` is not accepted. Configuration validation also
accepts HTTPS URLs, but this checkpoint's local login cannot serve HTTPS callbacks.

For the editable installation, settings default to the checkout's `.env`, even
when launched from another directory. Regular installations default to `.env` in
the launch directory. Process environment variables override file settings,
including empty values. To select a file explicitly, place `--env-file` before
the command:

```sh
myenv/bin/python -m tune_sync --env-file /absolute/path/to/.env spotify status
```

### Commands

```sh
# Local validation only: no browser, account requests, or playlist changes.
myenv/bin/python -m tune_sync

# Open Spotify authorization, receive the callback, and verify the account.
myenv/bin/python -m tune_sync spotify connect

# Verify the saved account; refresh expired credentials without opening a browser.
myenv/bin/python -m tune_sync spotify status

# List playlist metadata, following every page.
myenv/bin/python -m tune_sync spotify playlists

# Emit a single JSON document for inspection or later integration.
myenv/bin/python -m tune_sync spotify playlists --json

# Read all entries of a selected playlist (replace PLAYLIST_ID).
myenv/bin/python -m tune_sync spotify playlist PLAYLIST_ID
myenv/bin/python -m tune_sync spotify playlist PLAYLIST_ID --json

# Remove the local connection for this client ID.
myenv/bin/python -m tune_sync spotify disconnect
```

`myenv/bin/tune-sync` is an equivalent entry point. The old `python src/app.py`
entry point has been replaced.

Connect requests `user-read-private`, `playlist-read-private`, and
`playlist-read-collaborative`. Existing Step 3 connections must run connect again
to grant the added read permissions. Status still works with the old profile-only
permissions; playlists reports a reconnect instruction without opening a browser.
No write permissions are requested. The callback listener runs only during login, binds only to loopback,
validates the callback path and OAuth state, and closes after success, denial,
Ctrl-C, or a three-minute timeout. If the browser does not open, visit the printed
Spotify authorization URL. A busy callback port produces an actionable error.

Connect always starts a fresh authorization attempt. It replaces the existing
connection only after Spotify returns an account profile successfully. Confirm
the displayed account after login; to switch accounts, sign into the desired
Spotify account in your browser and run connect again. Status never starts login.
It detects a different account instead of silently replacing the saved identity.

Status without a saved connection reports “not connected” and exits successfully;
a failed verification exits with status 1. Ctrl-C during a connection command
exits with status 130. Transient network failures leave the saved connection in
place. Revoked authorization requires a new connect command.

### Credential storage

Tokens are stored as permission-protected JSON outside the repository, under:

- macOS: `~/Library/Application Support/tune-sync/spotify/`
- Linux: `~/.local/share/tune-sync/spotify/`

Files are separated by a hash of the client ID. The credential directory is
restricted to its owner (`0700`); token files are created with owner-only access
(`0600`) and replaced atomically. This is local file storage, not encrypted OS
Keychain storage. Concurrent connection commands for the same client ID are
blocked to prevent refresh/write races.

Use `spotify connect --data-dir /path/to/tune-sync-data` to select another base
directory. Use the same override for status and disconnect. Keep this directory
outside the repository. Only its dedicated `spotify/` child receives credentials.

The old project-root `.spotify_cache` is neither imported nor changed. Disconnect
removes only the new local token record; it does not revoke Spotify's app grant or
remove the legacy cache. To revoke provider authorization, remove the app from
your Spotify account's Apps page.

### Playlist snapshots and Step 5 manual checks

Use a 22-character playlist ID from `spotify playlists`, then run:

```sh
myenv/bin/python -m tune_sync spotify playlist PLAYLIST_ID
myenv/bin/python -m tune_sync spotify playlist PLAYLIST_ID --json
```

No additional permissions are needed if you connected in Step 4. Keep using the
same `--data-dir` override if you previously selected one. IDs, rather than share
URLs or Spotify URIs, are accepted in this checkpoint.

The command reads playlist metadata, all item pages (up to 50 entries per request),
and metadata again. It checks that the playlist version and selected metadata
have not changed, all page totals agree, and positions are contiguous. A missing
version, changed playlist, truncated page, or failed request produces an error
without printing a partial result. These checks detect changes reported by Spotify;
they do not lock the remote playlist or guarantee that it stays unchanged afterward.

The JSON result contains account identity, provider, playlist metadata, description,
Spotify `snapshot_id`, a UTC `captured_at` time, source country when available, a
SHA-256 fingerprint, and ordered `entries`. The fingerprint covers normalized
metadata and entries, excluding capture time. It is a local comparison aid, not
a Spotify version identifier. Output is not automatically saved to disk.

Each entry has a **zero-based** source `position`, status, reason, local-file flag,
added timestamp when available, and item metadata. Human-readable numbering starts
at 1. Intentional repeated songs remain separate entries, including across page
boundaries. Null/unavailable items keep their positions instead of disappearing.

| Status | Meaning at this checkpoint |
| --- | --- |
| `supported` | A catalog track with no explicit source restriction reported; not a guarantee of playability or a destination match |
| `unavailable` | Missing item, missing catalog ID, or explicitly unplayable/restricted source item |
| `unsupported` | Local file, episode, or unknown media type; retained for reporting |

Available item metadata includes title, all credited artist names, album, duration,
ISRC, explicit flag, provider ID/URI/URL, reported playability, restriction reason,
and the original track ID if Spotify relinked it. Missing metadata stays unknown;
version words such as “Live” and “Remix” are preserved. An unavailable source track
may still be matchable on another service later. No matching happens in this step.

Manual acceptance:

1. Read an owned playlist and compare its order/count with Spotify's native playlist
   order (disable any custom title/artist sort before comparing).
2. Read a playlist with more than 50 entries if you have one; check the boundary
   around entries 50 and 51 and the final entry.
3. Check a repeated song if available; it should appear in both positions.
4. Check an empty playlist or one with a local file, unavailable item, or episode
   if available. Offline tests cover these cases, so no fixture playlist is required.
5. Inspect `--json` for metadata and zero-based positions. Both JSON and the default
   output represent the same complete snapshot.
6. A followed playlist may return an access error. Spotify can list its metadata
   while refusing content access; the command reports this instead of claiming it
   is empty. Try an owned or accessible collaborative playlist for the main test.

Avoid editing the playlist during the main comparison. If it changes while being
read, the command asks you to retry rather than silently combining versions.

### Playlist listing and Step 4 manual checks

```sh
# Existing connections need the new read permissions.
myenv/bin/python -m tune_sync spotify connect
myenv/bin/python -m tune_sync spotify playlists
myenv/bin/python -m tune_sync spotify playlists --json
```

Compare the result with the playlists owned or followed in your Spotify library,
including private and collaborative playlists where available. If you have more
than 50 playlists, check that entries beyond the first 50 appear. Pagination is
also tested offline with 51 playlists, so you do not need to create test playlists.
Avoid editing your library during this comparison: paginated listings are not an
atomic snapshot and Spotify's API order need not match the app's current sort.

The default output shows names, IDs, owners, item counts, and URLs. Names are
quoted/escaped to keep terminal control characters harmless. The JSON output is
one object containing `provider`, `account`, `count`, and `playlists`. Each playlist
has these stable fields:

| Field | Meaning |
| --- | --- |
| `id`, `name`, `url` | Spotify playlist identity and link |
| `owner_id`, `owner_name` | Available owner metadata |
| `is_owned` | Whether the owner ID matches the connected user's Spotify ID |
| `public`, `collaborative` | Flags returned by Spotify, without inventing missing values |
| `item_count` | Reported item count, supporting both current `items` and legacy `tracks` metadata |
| `contents_access` | Always `not_checked` at this step |

Optional missing fields are `null`; missing counts are not converted to zero.
A collaborative flag does not establish that this user is a collaborator.
Followed playlists remain in the listing even if their contents may be restricted.
This command fetches metadata only, not tracks, liked songs, or individual playlist
contents. API restrictions mean it cannot promise that every playlist visible
elsewhere in the Spotify app is returned by this endpoint.

An empty library succeeds with count 0 and an empty array. An unconnected account,
missing permissions, malformed pagination, repeated playlist IDs, or a failed page
produces an error on stderr and exit status 1. Results are printed only after all
pages succeed, so a failed request does not emit a misleading partial JSON list.
For a rate limit, wait before retrying; automatic retry scheduling is a later step.

If you previously selected `--data-dir`, use the same option for playlists.
To verify that local validation is still side-effect-free, run plain startup again.

### Connection manual checks

1. Run plain startup. Expect successful local validation and no browser.
2. Run `spotify status`. On first use, expect “not connected”.
3. Run `spotify connect`, authorize in the browser, and return to the terminal.
   Confirm the displayed name and account identifier match the intended account.
4. Run `spotify status` in a fresh terminal. Expect the same account without login.
5. Run `spotify connect` again and deny authorization. Expect a clear error; status
   should still verify the previous connection if its grant remains valid.
6. Run `spotify disconnect`, then status. Expect “not connected”. Reconnect to
   restore access.
7. For account switching, log into another allowed Spotify account in the browser,
   connect again, and confirm status identifies the newly selected account.
8. Optional: after the token expires, status should still work without login.
   Refresh behavior is also covered by offline tests, so waiting is not required.

No command in this checkpoint creates, edits, or deletes playlists. If Spotify
returns 403, check that the user is allowlisted for the development app and that
the app owner meets Spotify's Premium requirement. If the registered callback
and configured callback differ, correct them and retry connect.

Configuration errors can be tested without editing `.env`:

```sh
SPOTIFY_CLIENT_ID='' myenv/bin/python -m tune_sync
SPOTIFY_CALLBACK_URL='http://localhost:8000/callback' myenv/bin/python -m tune_sync
```

### Automated checks and structure

```sh
myenv/bin/python -m unittest discover -s tests -v
```

Tests use temporary stores and fake Spotify responses. Callback tests start a
short-lived loopback server; no test contacts Spotify or uses real credentials.

`pyproject.toml` declares direct dependencies. `requirements.txt` is an editable
installation shortcut, not a lockfile. Application code is in `src/tune_sync/`:
`app.py` dispatches commands, `conf.py` validates settings, `auth.py` owns
connection handling, `playlists.py` normalizes paginated playlist metadata, and
`snapshots.py` reads and verifies complete playlist snapshots. Tests import the installed package normally.

The original search/create prototype operations remain in `spotify.py`, with an
explicit client argument. The old printing-only playlist/liked-song routine has
been replaced by the structured listing. `create_spotify_client(access_token)` creates a noninteractive client.
Those prototype operations are not exposed by the CLI; matching and write
improvements remain separate planned steps.

### API references

- [Spotify PKCE flow](https://developer.spotify.com/documentation/web-api/tutorials/code-pkce-flow)
- [Redirect URI requirements](https://developer.spotify.com/documentation/web-api/concepts/redirect_uri)
- [Current-user profile](https://developer.spotify.com/documentation/web-api/reference/get-current-users-profile)
- [Development-mode requirements](https://developer.spotify.com/documentation/web-api/concepts/quota-modes)

- [Current user playlists and pagination](https://developer.spotify.com/documentation/web-api/reference/get-a-list-of-current-users-playlists)

- [Playlist items](https://developer.spotify.com/documentation/web-api/reference/get-playlists-items)
- [Playlist metadata and snapshot IDs](https://developer.spotify.com/documentation/web-api/reference/get-playlist)

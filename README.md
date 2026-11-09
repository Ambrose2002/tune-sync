# Tune Sync

A tool for transferring playlists between music streaming services.

## Current checkpoint: package and explicit dependencies

The project currently contains a Spotify prototype. Running the application only
validates local settings. It does not open a login page, fetch your library, or
create a playlist. Cross-service transfers are not implemented yet.

### Setup

Use the existing `myenv` environment, or create one and install dependencies:

```sh
python3 -m venv myenv
myenv/bin/python -m pip install -e .
```

If you do not already have `.env`, copy `.env.example` to `.env`. Fill in
`SPOTIFY_CLIENT_ID` and `SPOTIFY_CLIENT_SECRET` from your Spotify app settings.
Set `SPOTIFY_CALLBACK_URL` to a redirect URI registered in that app. For local
development, the example uses `http://127.0.0.1:8000/callback`; `localhost` is not
accepted. This checkpoint does not start a callback server.

For the editable install above, the project-root `.env` is loaded regardless of
the working directory. A regular (non-editable) installation uses `.env` in the
launch directory. Pass `--env-file /absolute/path/to/.env` to select a file
explicitly; an explicitly selected file must exist. Exported
environment variables override it, including empty values. Settings are checked
for missing values, example placeholders, and invalid redirect URLs. This is
local validation, not verification that Spotify accepts the credentials.

```sh
myenv/bin/python -m tune_sync
```

Valid settings print a success message and exit with status 0. Invalid settings
print the affected variable names and exit with status 1, without printing
credential values. Your existing `.env` and token cache are not modified.

### Manual checks

1. Run the command above with valid settings. Confirm that no browser opens and
   no playlist is created.
2. Run `SPOTIFY_CLIENT_ID='' myenv/bin/python -m tune_sync`. Expect a clear missing
   client-ID error. This overrides the value only for this invocation.
3. Run `SPOTIFY_CALLBACK_URL='http://localhost:8000/callback' myenv/bin/python -m tune_sync`.
   Expect an error explaining the supported redirect URLs.
4. Run the original command again. It should succeed; `.env` was not changed.

### Automated checks

```sh
myenv/bin/python -m unittest discover -s tests -v
```

The checks use fake settings and block Spotify client construction. They do not
authenticate or use your real credentials.

### Package layout and entry points

Install with `myenv/bin/python -m pip install -e .` even if you already have the
dependencies from Step 1. The editable install makes source edits available
immediately. `pyproject.toml` declares the direct dependencies; `requirements.txt`
is a compatibility shortcut to that installation, not a dependency lockfile.

Both entry points perform the same local validation:

```sh
myenv/bin/python -m tune_sync
myenv/bin/tune-sync
myenv/bin/python -m tune_sync --env-file /absolute/path/to/.env
```

The old `python src/app.py` entry point has been replaced. Application code lives
in `src/tune_sync/`; tests import the installed package normally.

The existing prototype functions in `tune_sync.spotify` now take a Spotify client
as their first argument. `create_spotify_client(settings, cache_path=...)` builds
a client only when explicitly called. It does not load configuration or select
a cache path itself. Startup never calls it. The prototype's matching and
playlist behavior remain unchanged; their improvements belong to later steps.

For Step 2 acceptance, run both entry points, repeat the invalid-setting checks
above, and run the automated tests. From another directory, invoke the installed
`myenv/bin/python` by its absolute path with `-m tune_sync` and confirm that the
editable installation still finds the checkout's settings.

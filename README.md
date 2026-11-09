# Tune Sync

A tool for transferring playlists between music streaming services.

## Current checkpoint: safe startup and configuration

The project currently contains a Spotify prototype. Running the application only
validates local settings. It does not open a login page, fetch your library, or
create a playlist. Cross-service transfers are not implemented yet.

### Setup

Use the existing `myenv` environment, or create one and install dependencies:

```sh
python3 -m venv myenv
myenv/bin/python -m pip install -r requirements.txt
```

If you do not already have `.env`, copy `.env.example` to `.env`. Fill in
`SPOTIFY_CLIENT_ID` and `SPOTIFY_CLIENT_SECRET` from your Spotify app settings.
Set `SPOTIFY_CALLBACK_URL` to a redirect URI registered in that app. For local
development, the example uses `http://127.0.0.1:8000/callback`; `localhost` is not
accepted. This checkpoint does not start a callback server.

The project-root `.env` is loaded regardless of the working directory. Exported
environment variables override it, including empty values. Settings are checked
for missing values, example placeholders, and invalid redirect URLs. This is
local validation, not verification that Spotify accepts the credentials.

```sh
myenv/bin/python src/app.py
```

Valid settings print a success message and exit with status 0. Invalid settings
print the affected variable names and exit with status 1, without printing
credential values. Your existing `.env` and token cache are not modified.

### Manual checks

1. Run the command above with valid settings. Confirm that no browser opens and
   no playlist is created.
2. Run `SPOTIFY_CLIENT_ID='' myenv/bin/python src/app.py`. Expect a clear missing
   client-ID error. This overrides the value only for this invocation.
3. Run `SPOTIFY_CALLBACK_URL='http://localhost:8000/callback' myenv/bin/python src/app.py`.
   Expect an error explaining the supported redirect URLs.
4. Run the original command again. It should succeed; `.env` was not changed.

### Automated checks

```sh
myenv/bin/python -m unittest discover -s tests -v
```

The checks use fake settings and block Spotify client construction. They do not
authenticate or use your real credentials.

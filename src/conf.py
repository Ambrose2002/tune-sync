"""Load and validate local settings without exposing credential values."""

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class ConfigurationError(ValueError):
    """Settings are incomplete or invalid."""


@dataclass(frozen=True, repr=False)
class Conf:
    spotify_client_id: str
    spotify_client_secret: str
    spotify_callback_url: str


def load_conf(env_file=None):
    """Read the project .env; process environment variables take precedence."""
    path = PROJECT_ROOT / ".env" if env_file is None else env_file
    values = {**dotenv_values(path, interpolate=False), **os.environ}
    names = ("SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET", "SPOTIFY_CALLBACK_URL")
    settings = {name: (values.get(name) or "").strip() for name in names}
    errors = []
    for name, value in settings.items():
        if not value or value.startswith("your-spotify-"):
            errors.append(f"{name} is required; set it in .env or your environment.")

    callback = settings["SPOTIFY_CALLBACK_URL"]
    if callback and not callback.startswith("your-spotify-"):
        try:
            url = urlsplit(callback)
            # Accessing port also validates malformed/out-of-range ports.
            url.port
            valid = (
                bool(url.hostname)
                and url.hostname.lower() != "localhost"
                and url.username is None
                and url.password is None
                and not url.fragment
                and not any(character.isspace() for character in callback)
                and (url.scheme == "https" or (
                    url.scheme == "http" and url.hostname in ("127.0.0.1", "::1")
                ))
            )
        except ValueError:
            valid = False
        if not valid:
            errors.append(
                "SPOTIFY_CALLBACK_URL must be an HTTPS URL or an HTTP loopback "
                "URL using 127.0.0.1 or [::1], without credentials or a fragment. "
                "Register the same redirect URI in Spotify's developer dashboard."
            )

    if errors:
        raise ConfigurationError("\n".join(errors))
    return Conf(*(settings[name] for name in names))

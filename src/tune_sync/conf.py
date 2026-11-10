"""Load and validate local settings without exposing credential values."""

import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class ConfigurationError(ValueError):
    """Settings are incomplete or invalid."""


@dataclass(frozen=True, repr=False)
class Conf:
    spotify_client_id: str
    spotify_callback_url: str


def _load_values(env_file=None):
    """Read settings without changing the process environment."""
    # Editable installs use the checkout; regular installs use the launch directory.
    default_root = PROJECT_ROOT if (PROJECT_ROOT / "pyproject.toml").is_file() else Path.cwd()
    path = default_root / ".env" if env_file is None else env_file
    path = Path(path).expanduser().resolve()
    return {**dotenv_values(path, interpolate=False), **os.environ}, path.parent


def load_conf(env_file=None):
    """Read Spotify settings; process environment variables take precedence."""
    values, _ = _load_values(env_file)
    names = ("SPOTIFY_CLIENT_ID", "SPOTIFY_CALLBACK_URL")
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


@dataclass(frozen=True, repr=False)
class AppleConf:
    team_id: str
    key_id: str
    private_key_path: Path
    storefront: str


def validate_storefront(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z]{2}", value):
        raise ConfigurationError("APPLE_STOREFRONT must be a two-letter storefront code, such as us or gb.")
    return value.lower()


def load_apple_conf(env_file=None):
    """Validate Apple settings independently of Spotify credentials."""
    values, directory = _load_values(env_file)
    errors = []
    identifiers = {}
    for name in ("APPLE_TEAM_ID", "APPLE_KEY_ID"):
        value = (values.get(name) or "").strip()
        if not re.fullmatch(r"[A-Z0-9]{10}", value):
            errors.append(f"{name} must be your 10-character Apple developer identifier.")
        identifiers[name] = value
    key_path = (values.get("APPLE_PRIVATE_KEY_PATH") or "").strip()
    if not key_path or key_path.startswith("your-"):
        errors.append("APPLE_PRIVATE_KEY_PATH must point to your downloaded MusicKit .p8 private key.")
    try:
        storefront = validate_storefront((values.get("APPLE_STOREFRONT", "us") or "").strip())
    except ConfigurationError as error:
        errors.append(str(error))
    if errors:
        raise ConfigurationError("\n".join(errors))
    path = Path(key_path).expanduser()
    if not path.is_absolute():
        path = directory / path
    return AppleConf(identifiers["APPLE_TEAM_ID"], identifiers["APPLE_KEY_ID"], path, storefront)

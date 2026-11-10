"""Apple developer-token signing and read-only catalog connectivity."""

from dataclasses import dataclass
import re
import time

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
import jwt
import requests

from .conf import ConfigurationError, validate_storefront

TOKEN_LIFETIME_SECONDS = 3600


class AppleAPIError(ValueError):
    """A credential-free description of an Apple catalog failure."""


@dataclass(frozen=True)
class CatalogSong:
    id: str
    storefront: str
    name: str | None
    artist_name: str | None
    album_name: str | None
    duration_ms: int | None
    isrc: str | None
    content_rating: str | None
    url: str | None


def generate_developer_token(settings):
    """Sign a one-hour ES256 JWT in memory; never persist or print it."""
    try:
        pem = settings.private_key_path.read_bytes()
    except (OSError, ValueError):
        raise ConfigurationError("Cannot read APPLE_PRIVATE_KEY_PATH. Check that the MusicKit key file exists and is readable.") from None
    try:
        key = serialization.load_pem_private_key(pem, password=None)
    except (ValueError, TypeError, UnsupportedAlgorithm):
        raise ConfigurationError("APPLE_PRIVATE_KEY_PATH must contain a valid, unencrypted MusicKit private key.") from None
    if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
        raise ConfigurationError("Apple requires an elliptic-curve P-256 private key for ES256 signing.")
    now = int(time.time())
    try:
        return jwt.encode({"iss": settings.team_id, "iat": now, "exp": now + TOKEN_LIFETIME_SECONDS},
                          key, algorithm="ES256", headers={"kid": settings.key_id})
    except (jwt.PyJWTError, ValueError, TypeError):
        raise ConfigurationError("Unable to sign the Apple developer token. Check the MusicKit key.") from None


def validate_song_id(song_id):
    if not isinstance(song_id, str) or not re.fullmatch(r"[0-9]+", song_id):
        raise ConfigurationError("Provide a numeric Apple catalog song ID, not a library ID or URL.")
    return song_id


def get_catalog_song(developer_token, song_id, *, storefront, session):
    """Fetch one song through an injected HTTP session; no user token is needed."""
    validate_song_id(song_id)
    storefront = validate_storefront(storefront)
    try:
        response = session.get(
            f"https://api.music.apple.com/v1/catalog/{storefront}/songs/{song_id}",
            headers={"Authorization": f"Bearer {developer_token}", "Accept": "application/json"},
            timeout=15, allow_redirects=False,
        )
    except requests.exceptions.RequestException:
        raise AppleAPIError("Could not reach Apple Music. Check your connection and try again.") from None
    if response.status_code != 200:
        messages = {
            400: "Apple rejected the catalog request. Check the song ID and storefront.",
            401: "Apple rejected the developer token. Check the Team ID, Key ID, MusicKit key, and system clock.",
            403: "Apple denied catalog access. Check that MusicKit is enabled for this developer key.",
            404: "Song not found in this storefront. Check the catalog song ID and region.",
            429: "Apple Music is rate-limiting requests. Wait before trying again.",
        }
        raise AppleAPIError(messages.get(response.status_code, "Apple Music could not complete the catalog request. Try again later."))
    try:
        body = response.json()
    except ValueError:
        raise AppleAPIError("Apple Music returned an invalid JSON response. Try again later.") from None
    data = body.get("data") if isinstance(body, dict) else None
    if data == []:
        raise AppleAPIError("Song not found in this storefront. Check the catalog song ID and region.")
    if (not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict)
            or data[0].get("type") != "songs" or data[0].get("id") != song_id
            or not isinstance(data[0].get("attributes"), dict)):
        raise AppleAPIError("Apple Music returned incomplete or unexpected song data. Try again later.")
    attributes = data[0]["attributes"]
    def text(name):
        value = attributes.get(name)
        return value if isinstance(value, str) else None
    duration = attributes.get("durationInMillis")
    return CatalogSong(song_id, storefront, text("name"), text("artistName"), text("albumName"),
                       duration if type(duration) is int and duration >= 0 else None,
                       text("isrc"), text("contentRating"), text("url"))

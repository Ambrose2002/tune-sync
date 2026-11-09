"""Read a version-checked Spotify playlist without losing entry positions."""

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
import re
from urllib.parse import parse_qs, urlsplit

from spotipy.exceptions import SpotifyException

from .playlists import PlaylistReadError, PlaylistSummary, normalize_playlist

METADATA_FIELDS = "id,name,description,owner,external_urls,public,collaborative,snapshot_id"


@dataclass(frozen=True)
class MediaItem:
    id: str | None
    uri: str | None
    type: str | None
    name: str | None
    artists: tuple[str, ...]
    album: str | None
    duration_ms: int | None
    isrc: str | None
    explicit: bool | None
    is_playable: bool | None
    restriction_reason: str | None
    original_id: str | None
    url: str | None


@dataclass(frozen=True)
class PlaylistEntry:
    position: int  # Zero-based source position; repeated tracks remain distinct entries.
    status: str
    reason: str | None
    is_local: bool | None
    added_at: str | None
    item: MediaItem | None


@dataclass(frozen=True)
class PlaylistSnapshot:
    provider: str
    playlist: PlaylistSummary
    description: str | None
    snapshot_id: str
    captured_at: str
    source_country: str | None
    fingerprint: str
    entries: tuple[PlaylistEntry, ...]


def _text(value):
    return value if isinstance(value, str) else None


def _object(value):
    return value if isinstance(value, dict) else {}


def _boolean(value):
    return value if isinstance(value, bool) else None


def validate_playlist_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9]{22}", value):
        raise PlaylistReadError("Provide the 22-character Spotify playlist ID shown by spotify playlists.")
    return value


def normalize_entry(raw, position):
    if raw is None:
        return PlaylistEntry(position, "unavailable", "missing_item", None, None, None)
    if not isinstance(raw, dict) or not ("item" in raw or "track" in raw):
        raise PlaylistReadError("Spotify returned an invalid playlist entry. Retry the snapshot.")
    # A null current field must not be replaced with a stale legacy value.
    payload = raw["item"] if "item" in raw else raw["track"]
    if payload is not None and not isinstance(payload, dict):
        raise PlaylistReadError("Spotify returned an invalid media item. Retry the snapshot.")
    data = _object(payload)
    local_values = (_boolean(raw.get("is_local")), _boolean(data.get("is_local")))
    is_local = True if True in local_values else False if False in local_values else None
    item = None
    if payload is not None:
        artists = data.get("artists") if isinstance(data.get("artists"), list) else []
        duration = data.get("duration_ms")
        item = MediaItem(
            id=_text(data.get("id")), uri=_text(data.get("uri")), type=_text(data.get("type")),
            name=_text(data.get("name")),
            artists=tuple(artist["name"] for artist in artists
                          if isinstance(artist, dict) and isinstance(artist.get("name"), str)),
            album=_text(_object(data.get("album")).get("name")),
            duration_ms=duration if type(duration) is int and duration >= 0 else None,
            isrc=_text(_object(data.get("external_ids")).get("isrc")),
            explicit=_boolean(data.get("explicit")), is_playable=_boolean(data.get("is_playable")),
            restriction_reason=_text(_object(data.get("restrictions")).get("reason")),
            original_id=_text(_object(data.get("linked_from")).get("id")),
            url=_text(_object(data.get("external_urls")).get("spotify")),
        )
    if is_local:
        status, reason = "unsupported", "local_file"
    elif item is None:
        status, reason = "unavailable", "missing_item"
    elif item.type != "track":
        status, reason = "unsupported", "episode" if item.type == "episode" else "unknown_type"
    elif not item.id:
        status, reason = "unavailable", "missing_catalog_id"
    elif item.is_playable is False or item.restriction_reason:
        status, reason = "unavailable", item.restriction_reason or "not_playable"
    else:
        status, reason = "supported", None
    return PlaylistEntry(position, status, reason, is_local, _text(raw.get("added_at")), item)


def read_playlist_snapshot(client, playlist_id, *, user_id, source_country=None):
    validate_playlist_id(playlist_id)
    try:
        return _read_snapshot(client, playlist_id, user_id, source_country)
    except SpotifyException as error:
        if error.http_status in (403, 404):
            raise PlaylistReadError(
                "Spotify did not allow this playlist to be read, or it no longer exists. "
                "Try a playlist you own or collaborate on; appearing in your library does not guarantee content access."
            ) from None
        raise


def _metadata(client, playlist_id, user_id):
    raw = client.playlist(playlist_id, fields=METADATA_FIELDS)
    summary = normalize_playlist(raw, user_id)
    if summary.id != playlist_id or not isinstance(raw.get("snapshot_id"), str) or not raw["snapshot_id"]:
        raise PlaylistReadError("Spotify did not return a usable playlist version. Cannot verify a complete snapshot.")
    return summary, _text(raw.get("description")), raw["snapshot_id"]


def _read_snapshot(client, playlist_id, user_id, source_country):
    before = _metadata(client, playlist_id, user_id)
    entries = []
    total = None
    offset = 0
    while True:
        page = client.playlist_items(playlist_id, limit=50, offset=offset,
                                     additional_types=("track", "episode"))
        if (not isinstance(page, dict) or not isinstance(page.get("items"), list)
                or "next" not in page or type(page.get("total")) is not int
                or page["total"] < 0 or type(page.get("offset")) is not int
                or page["offset"] != offset):
            raise PlaylistReadError("Spotify returned an incomplete or misaligned item page. Retry the snapshot.")
        if total is None:
            total = page["total"]
        elif total != page["total"]:
            raise PlaylistReadError("Playlist length changed while reading. Retry the snapshot.")
        entries.extend(normalize_entry(raw, offset + index) for index, raw in enumerate(page["items"]))
        if len(entries) > total:
            raise PlaylistReadError("Playlist item counts are inconsistent. Retry the snapshot.")
        if page["next"] is None:
            if len(entries) != total:
                raise PlaylistReadError("Spotify returned a truncated playlist. Retry the snapshot.")
            break
        try:
            url = urlsplit(page["next"])
            offsets = parse_qs(url.query).get("offset", [])
            next_offset = int(offsets[0]) if len(offsets) == 1 else -1
            valid = (url.scheme == "https" and url.netloc == "api.spotify.com"
                     and url.path in (f"/v1/playlists/{playlist_id}/items", f"/v1/playlists/{playlist_id}/tracks")
                     and not url.fragment and bool(page["items"])
                     and next_offset == len(entries) and next_offset < total)
        except (ValueError, TypeError, AttributeError):
            valid = False
        if not valid:
            raise PlaylistReadError("Spotify returned invalid item pagination. Retry the snapshot.")
        offset = next_offset
    after = _metadata(client, playlist_id, user_id)
    if before != after:
        raise PlaylistReadError("Playlist changed while reading. Retry when edits have finished.")
    summary, description, version = before
    summary = replace(summary, item_count=len(entries), contents_access="read")
    fingerprint_data = {"playlist": asdict(summary), "description": description,
                        "snapshot_id": version, "source_country": source_country,
                        "entries": [asdict(entry) for entry in entries]}
    fingerprint = hashlib.sha256(json.dumps(fingerprint_data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return PlaylistSnapshot("spotify", summary, description, version,
                            datetime.now(timezone.utc).isoformat(), source_country,
                            fingerprint, tuple(entries))

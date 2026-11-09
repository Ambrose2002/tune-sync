"""Read and normalize Spotify playlist metadata without fetching contents."""

from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit


class PlaylistReadError(ValueError):
    """The complete playlist listing could not be read reliably."""


@dataclass(frozen=True)
class PlaylistSummary:
    id: str
    name: str | None
    owner_id: str | None
    owner_name: str | None
    url: str | None
    public: bool | None
    collaborative: bool | None
    is_owned: bool | None
    item_count: int | None
    contents_access: str = "not_checked"


def _text(value):
    return value if isinstance(value, str) else None


def _boolean(value):
    return value if isinstance(value, bool) else None


def normalize_playlist(raw, user_id):
    if not isinstance(raw, dict) or not isinstance(raw.get("id"), str) or not raw["id"]:
        raise PlaylistReadError("Spotify returned a playlist without an ID. Retry the listing.")
    owner = raw.get("owner") if isinstance(raw.get("owner"), dict) else {}
    links = raw.get("external_urls") if isinstance(raw.get("external_urls"), dict) else {}
    owner_id = _text(owner.get("id"))
    count = None
    # Prefer the current response field, with support for older app responses.
    for field in ("items", "tracks"):
        collection = raw.get(field)
        total = collection.get("total") if isinstance(collection, dict) else None
        if type(total) is int and total >= 0:
            count = total
            break
    return PlaylistSummary(
        id=raw["id"], name=_text(raw.get("name")), owner_id=owner_id,
        owner_name=_text(owner.get("display_name")), url=_text(links.get("spotify")),
        public=_boolean(raw.get("public")), collaborative=_boolean(raw.get("collaborative")),
        is_owned=owner_id == user_id if owner_id and user_id else None, item_count=count,
    )


def list_playlists(client, *, user_id):
    """Return every available playlist in API order, or fail without partial output."""
    result = []
    ids = set()
    offset = 0
    while True:
        page = client.current_user_playlists(limit=50, offset=offset)
        if not isinstance(page, dict) or not isinstance(page.get("items"), list) or "next" not in page:
            raise PlaylistReadError("Spotify returned an incomplete playlist page. Retry the listing.")
        for raw in page["items"]:
            playlist = normalize_playlist(raw, user_id)
            if playlist.id in ids:
                raise PlaylistReadError("A playlist repeated during pagination. The library may have changed; retry the listing.")
            ids.add(playlist.id)
            result.append(playlist)
        if page["next"] is None:
            return result
        try:
            url = urlsplit(page["next"])
            offsets = parse_qs(url.query).get("offset", [])
            next_offset = int(offsets[0]) if len(offsets) == 1 else -1
            valid = (url.scheme == "https" and url.netloc == "api.spotify.com"
                     and url.path == "/v1/me/playlists" and not url.fragment
                     and offset < next_offset <= 100000)
        except (ValueError, TypeError, AttributeError):
            valid = False
        if not valid:
            raise PlaylistReadError("Spotify returned invalid playlist pagination. Retry the listing.")
        # Request the known endpoint rather than passing an arbitrary URL to the SDK.
        offset = next_offset

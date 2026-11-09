"""Existing Spotify prototype operations with an explicitly supplied client."""

import spotipy
from spotipy.oauth2 import SpotifyOAuth

from .conf import Conf

scope = (
    "user-read-private "
    "playlist-read-private "
    "playlist-read-collaborative "
    "user-library-read "
    "playlist-modify-public "
    "playlist-modify-private"
)


def create_spotify_client(conf: Conf, *, cache_path):
    """Construct a client from explicit settings and a caller-selected cache path."""
    return spotipy.Spotify(auth_manager=SpotifyOAuth(
        client_id=conf.spotify_client_id,
        client_secret=conf.spotify_client_secret,
        redirect_uri=conf.spotify_callback_url,
        scope=scope,
        cache_path=str(cache_path)
    ))


def get_playlists(sp):
    """Print accessible current-user playlists and all liked songs."""

    playlists = sp.current_user_playlists()

    me = sp.current_user()["id"]

    for playlist in playlists["items"]:
        if playlist["owner"]["id"] != me and not playlist["collaborative"]:
            print("Skipping inaccessible playlist:", playlist["name"])
            continue

        items = sp.playlist_items(playlist["id"], limit=50)
        print(playlist["name"])

        while items:
            for item in items["items"]:
                # print(item.get("item").get("name"))
                continue

            if items["next"]:
                items = sp.next(items)
            else:
                items = None

    liked = sp.current_user_saved_tracks(limit=50)

    while liked:
        for item in liked["items"]:
            track = item["track"]
            print(track["name"], "-", track["artists"][0]["name"])

        if liked["next"]:
            liked = sp.next(liked)
        else:
            liked = None


def search_track_uri(sp, track_name):
    """Return the Spotify URI for the first track search result, or None."""

    result = sp.search(q=track_name, type="track", limit=1)
    tracks = result["tracks"]["items"]

    if not tracks:
        return None

    return tracks[0]["uri"]


def create_playlist(sp, name, items, public=True, collaborative=False, description=""):
    """Create a Spotify playlist from a list of track names and return it."""

    playlist = sp.current_user_playlist_create(name, public, collaborative, description)
    playlist_id = playlist["id"]

    uris = []

    for track_name in items:
        uri = search_track_uri(sp, track_name)
        if uri:
            uris.append(uri)

        else:
            print("Could not find:", track_name)

    if uris:
        result = sp.playlist_add_items(playlist_id, uris)
        print(result)

    return playlist


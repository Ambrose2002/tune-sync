"""Existing Spotify prototype operations with an explicitly supplied client."""

import spotipy


def create_spotify_client(access_token):
    """Construct a noninteractive client from an explicitly obtained access token."""
    return spotipy.Spotify(auth=access_token, requests_timeout=15, retries=0)


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


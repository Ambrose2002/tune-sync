import requests
import base64
import spotipy
from spotipy.oauth2 import SpotifyOAuth
import json

from conf import conf

scope = (
    "user-read-private "
    "playlist-read-private "
    "playlist-read-collaborative "
    "user-library-read "
    "playlist-modify-public "
    "playlist-modify-private"
)


sp = spotipy.Spotify(
    auth_manager=SpotifyOAuth(
        client_id=conf.spotify_client_id,
        client_secret=conf.spotify_client_secret,
        redirect_uri=conf.spotify_callback_url,
        scope=scope,
        cache_path=".spotify_cache"
    )
)


def get_playlists():

    playlists = sp.current_user_playlists()

    me = sp.current_user()["id"]

    for playlist in playlists["items"]:
        if playlist["owner"]["id"] != me and not playlist["collaborative"]:
            print("Skipping inaccessible playlist:", playlist["name"])
            continue

        items = sp.playlist_items(playlist["id"])
        print(playlist["name"])
        for item in items.get("items"):
            # print(item.get("item").get("name"))
            continue

    liked = sp.current_user_saved_tracks(limit=50)

    for item in liked["items"]:
        track = item["track"]
        print(track["name"], "-", track["artists"][0]["name"])


def search_track_uri(track_name):
    result = sp.search(q=track_name, type="track", limit=1)
    tracks = result["tracks"]["items"]

    if not tracks:
        return None

    return tracks[0]["uri"]


def create_playlist(name, items, public = True, collaborative=False, description=""):
    
    playlist = sp.current_user_playlist_create(name, public, collaborative, description)
    playlist_id = playlist["id"]
    
    uris = []
    
    for track_name in items:
        uri = search_track_uri(track_name)
        if uri:
            uris.append(uri)
            
        else:
            print("Could not find:", track_name)
    
    if uris:
        result = sp.playlist_add_items(playlist_id, uris)
        print(result)
    
    return playlist


if __name__ == "__main__":
    get_playlists()

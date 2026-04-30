import requests
import base64
import spotipy
from spotipy.oauth2 import SpotifyOAuth
import json

from conf import conf

scope = "user-read-private playlist-read-private playlist-read-collaborative user-library-read"

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


if __name__ == "__main__":
    get_playlists()

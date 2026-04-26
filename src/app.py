import requests
import base64
import spotipy
from spotipy.oauth2 import SpotifyOAuth
import json

from conf import conf

scope = "user-read-private playlist-read-private playlist-read-collaborative"

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
    
    user = sp.current_user()
    
    
    playlists = sp.current_user_playlists()
    print(len(playlists["items"]))
    print(playlists["items"])
    
    for playlist in playlists["items"]:
        print(json.dumps(playlist, indent=4))
        return
    
    # while playlists:
    #     for i, playlist in enumerate(playlists['items']):
    #         print(f"{i + 1 + playlists['offset']:4d} {playlist['uri']} {playlist['name']}")
    #     if playlists['next']:
    #         playlists = sp.next(playlists)
    #     else:
    #         playlists = None
    
    

if __name__ == "__main__":
    get_playlists()
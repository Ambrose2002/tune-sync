import os
from dotenv import load_dotenv

load_dotenv()

class Conf:
    spotify_client_secret = os.getenv("SPOTIFY_CLIENT_SECRET")
    spotify_client_id = os.getenv("SPOTIFY_CLIENT_ID")
    spotify_callback_url = os.getenv("SPOTIFY_CALLBACK_URL")
    
conf = Conf()
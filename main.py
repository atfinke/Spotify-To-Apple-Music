import os
import sys
import time
from typing import List, Tuple, Optional, Dict, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse, parse_qs

import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import requests
import jwt
from tqdm import tqdm
from ratelimit import limits, sleep_and_retry
from dotenv import load_dotenv
from cryptography.hazmat.primitives import serialization

# Load environment variables from .env file
load_dotenv()

# Type aliases
SpotifyTrack = Tuple[str, str, str]  # (track_name, artist_name, album_name)
AppleMusicTrackID = str

# API credentials
SPOTIFY_CLIENT_ID: str = os.getenv('SPOTIFY_CLIENT_ID', '')
SPOTIFY_CLIENT_SECRET: str = os.getenv('SPOTIFY_CLIENT_SECRET', '')
APPLE_MUSIC_KEY_ID: str = os.getenv('APPLE_MUSIC_KEY_ID', '')
APPLE_MUSIC_TEAM_ID: str = os.getenv('APPLE_MUSIC_TEAM_ID', '')
APPLE_MUSIC_SECRET_KEY: str = os.getenv('APPLE_MUSIC_SECRET_KEY', '').replace("\\n", "\n")
APPLE_MUSIC_USER_TOKEN: str = os.getenv('APPLE_MUSIC_USER_TOKEN', '')

# Constants
MAX_CONCURRENT_TASKS: int = 10
SPOTIFY_RATE_LIMIT: Tuple[int, int] = (20, 1)  # 20 calls per 1 second
APPLE_MUSIC_RATE_LIMIT: Tuple[int, int] = (20, 1)  # 20 calls per 1 second

def setup_credentials() -> None:
    """
    Verify that all required environment variables are set.
    
    Raises:
        SystemExit: If any required environment variable is missing.
    """
    required_vars = [
        'SPOTIFY_CLIENT_ID', 'SPOTIFY_CLIENT_SECRET', 'APPLE_MUSIC_KEY_ID',
        'APPLE_MUSIC_TEAM_ID', 'APPLE_MUSIC_SECRET_KEY', 'APPLE_MUSIC_USER_TOKEN'
    ]
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    if missing_vars:
        print(f"Error: Missing required environment variables: {', '.join(missing_vars)}")
        print("Please set all required environment variables in your .env file.")
        sys.exit(1)

def extract_playlist_id(playlist_url: str) -> str:
    """
    Extract the playlist ID from a Spotify playlist URL.
    
    Args:
        playlist_url (str): The Spotify playlist URL.
    
    Returns:
        str: The extracted playlist ID.
    
    Raises:
        ValueError: If the playlist ID cannot be extracted from the URL.
    """
    parsed_url = urlparse(playlist_url)
    path_segments = parsed_url.path.split('/')
    
    if 'playlist' in path_segments:
        playlist_index = path_segments.index('playlist')
        if playlist_index + 1 < len(path_segments):
            return path_segments[playlist_index + 1]
    
    # Check if the ID is in the query parameters (for shortened URLs)
    query_params = parse_qs(parsed_url.query)
    if 'si' in query_params:
        return query_params['si'][0]
    
    raise ValueError("Could not extract playlist ID from the provided URL.")

@sleep_and_retry
@limits(calls=SPOTIFY_RATE_LIMIT[0], period=SPOTIFY_RATE_LIMIT[1])
def get_spotify_tracks(playlist_id: str) -> Tuple[List[SpotifyTrack], str]:
    """
    Fetch all tracks from a Spotify playlist.
    
    Args:
        playlist_id (str): The Spotify playlist ID.
    
    Returns:
        Tuple[List[SpotifyTrack], str]: A tuple containing:
            - A list of tuples containing track names, artist names, and album names.
            - The name of the Spotify playlist.
    
    Raises:
        SystemExit: If there's an error accessing the Spotify API.
    """
    try:
        client_credentials_manager = SpotifyClientCredentials(
            client_id=SPOTIFY_CLIENT_ID,
            client_secret=SPOTIFY_CLIENT_SECRET
        )
        sp = spotipy.Spotify(client_credentials_manager=client_credentials_manager)
        
        playlist = sp.playlist(playlist_id)
        playlist_name = playlist['name']
        
        results = sp.playlist_tracks(playlist_id)
        tracks = results['items']
        while results['next']:
            results = sp.next(results)
            tracks.extend(results['items'])
        
        return [(track['track']['name'], track['track']['artists'][0]['name'], track['track']['album']['name']) for track in tracks if track['track']], playlist_name
    except spotipy.SpotifyException as e:
        print(f"Error accessing Spotify API: {e}")
        sys.exit(1)

def get_apple_music_token() -> str:
    """
    Generate an Apple Music API token.
    
    Returns:
        str: The generated Apple Music API token.
    
    Raises:
        SystemExit: If there's an error creating the Apple Music token.
    """
    try:
        # Load the private key
        private_key = serialization.load_pem_private_key(
            APPLE_MUSIC_SECRET_KEY.encode(),
            password=None
        )

        headers = {
            'alg': 'ES256',
            'kid': APPLE_MUSIC_KEY_ID
        }
        payload = {
            'iss': APPLE_MUSIC_TEAM_ID,
            'iat': int(time.time()),
            'exp': int(time.time()) + 15777000
        }
        
        token = jwt.encode(payload, private_key, algorithm='ES256', headers=headers)
        return token
    except Exception as e:
        print(f"Error creating Apple Music token: {e}")
        sys.exit(1)

@sleep_and_retry
@limits(calls=APPLE_MUSIC_RATE_LIMIT[0], period=APPLE_MUSIC_RATE_LIMIT[1])
def search_apple_music(track_name: str, artist_name: str, album_name: str, token: str) -> Optional[AppleMusicTrackID]:
    """
    Search for a track on Apple Music. If no result is found, try searching again without the album name.
    
    Args:
    track_name (str): The name of the track to search for.
    artist_name (str): The name of the artist.
    album_name (str): The name of the album.
    token (str): The Apple Music API token.
    
    Returns:
    Optional[AppleMusicTrackID]: The Apple Music track ID if found, None otherwise.
    """
    url = "https://api.music.apple.com/v1/catalog/us/search"
    headers = {
        'Authorization': f'Bearer {token}'
    }
    
    def perform_search(search_term):
        params = {
            'term': search_term,
            'types': 'songs',
            'limit': 1
        }
        try:
            response = requests.get(url, headers=headers, params=params)
            response.raise_for_status()
            data = response.json()
            if 'songs' in data['results'] and data['results']['songs']['data']:
                return data['results']['songs']['data'][0]['id']
            return None
        except requests.RequestException as e:
            print(f"Error searching Apple Music: {e}")
            return None
    
    # First search with all information
    result = perform_search(f"{track_name} {artist_name} {album_name}")
    
    # If no result, try again without album name
    if result is None:
        result = perform_search(f"{track_name} {artist_name}")
    
    return result

def create_apple_music_playlist(name: str, track_ids: List[AppleMusicTrackID], token: str) -> Dict[str, Any]:
    """
    Create a new playlist on Apple Music.
    
    Args:
        name (str): The name of the new playlist.
        track_ids (List[AppleMusicTrackID]): A list of Apple Music track IDs to add to the playlist.
        token (str): The Apple Music API token.
    
    Returns:
        Dict[str, Any]: The JSON response from the Apple Music API.
    
    Raises:
        SystemExit: If there's an error creating the Apple Music playlist.
    """
    url = "https://api.music.apple.com/v1/me/library/playlists"
    headers = {
        'Authorization': f'Bearer {token}',
        'Music-User-Token': APPLE_MUSIC_USER_TOKEN,
        'Content-Type': 'application/json'
    }
    data = {
        "attributes": {
            "name": name
        },
        "relationships": {
            "tracks": {
                "data": [{"id": track_id, "type": "songs"} for track_id in track_ids]
            }
        }
    }
    try:
        response = requests.post(url, headers=headers, json=data)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        print(f"Error creating Apple Music playlist: {e}")
        sys.exit(1)

def validate_apple_music_token(token: str) -> bool:
    """
    Validate the Apple Music user token.
    
    Args:
        token (str): The Apple Music user token to validate.
    
    Returns:
        bool: True if the token is valid, False otherwise.
    """
    url = "https://api.music.apple.com/v1/me/library/playlists"
    headers = {
        'Authorization': f'Bearer {get_apple_music_token()}',
        'Music-User-Token': token
    }
    try:
        response = requests.get(url, headers=headers)
        return response.status_code == 200
    except requests.RequestException:
        return False

def convert_playlist(spotify_playlist_url: str) -> Tuple[Dict[str, Any], int, int]:
    """
    Convert a Spotify playlist to an Apple Music playlist.
    
    Args:
        spotify_playlist_url (str): The URL of the Spotify playlist to convert.
    
    Returns:
        Tuple[Dict[str, Any], int, int]: A tuple containing:
            - The JSON response from creating the Apple Music playlist
            - The total number of tracks in the Spotify playlist
            - The number of tracks successfully transferred to Apple Music
    """
    setup_credentials()
    spotify_playlist_id = extract_playlist_id(spotify_playlist_url)
    spotify_tracks, spotify_playlist_name = get_spotify_tracks(spotify_playlist_id)
    apple_music_token = get_apple_music_token()
    
    new_playlist_name = f"{spotify_playlist_name} (Converted by Tool)"
    
    apple_music_track_ids: List[Optional[AppleMusicTrackID]] = [None] * len(spotify_tracks)
    not_found_tracks: List[SpotifyTrack] = []
    
    print(f"Converting {len(spotify_tracks)} tracks...")
    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_TASKS) as executor:
        future_to_index = {executor.submit(search_apple_music, track_name, artist_name, album_name, apple_music_token): i 
                   for i, (track_name, artist_name, album_name) in enumerate(spotify_tracks)}

        for future in tqdm(as_completed(future_to_index), total=len(future_to_index), unit="track"):
            index = future_to_index[future]
            track_name, artist_name, album_name = spotify_tracks[index]
            try:
                track_id = future.result()
                if track_id:
                    apple_music_track_ids[index] = track_id
                else:
                    not_found_tracks.append((track_name, artist_name, album_name))
            except Exception as e:
                print(f"Error processing track {track_name} by {artist_name}: {e}")
                not_found_tracks.append((track_name, artist_name, album_name))
    
    # Remove None values from apple_music_track_ids
    apple_music_track_ids = [track_id for track_id in apple_music_track_ids if track_id is not None]
    
    new_playlist = create_apple_music_playlist(new_playlist_name, apple_music_track_ids, apple_music_token)
    
    print("\nTracks not found on Apple Music:")
    for track_name, artist_name, album_name in not_found_tracks:
        print(f"- {track_name} by {artist_name} ({album_name})")
    
    return new_playlist, len(spotify_tracks), len(apple_music_track_ids)

def main() -> None:
    """
    Main function to run the Spotify to Apple Music playlist converter.
    """
    print("Spotify to Apple Music Playlist Converter")
    print("----------------------------------------")
    
    # Verify credentials before proceeding
    setup_credentials()
    
    # Validate Apple Music user token
    if not validate_apple_music_token(APPLE_MUSIC_USER_TOKEN):
        print("Error: Invalid Apple Music user token. Please check your APPLE_MUSIC_USER_TOKEN in the .env file.")
        sys.exit(1)
    
    spotify_playlist_url = input("Enter Spotify playlist URL: ")
    
    try:
        result, total_tracks, transferred_tracks = convert_playlist(spotify_playlist_url)
        print(f"\nNew Apple Music playlist created: {result['data'][0]['attributes']['name']}")
        print(f"Transferred {transferred_tracks} out of {total_tracks} tracks")
        print(f"Success rate: {transferred_tracks/total_tracks:.2%}")
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
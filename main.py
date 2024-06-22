"""
Spotify to Apple Music Playlist Converter

This script converts a Spotify playlist to an Apple Music playlist. It fetches tracks from a Spotify
playlist, searches for each track on Apple Music, and creates a new Apple Music playlist with the
matching tracks.

Requirements:
- Python 3.7+
- Required libraries: spotipy, requests, PyJWT, tqdm, ratelimit

Usage:
1. Set up the required environment variables (see below).
2. Run the script: python spotify_to_apple_music.py
3. Enter the Spotify playlist ID when prompted.
4. Enter the desired name for the new Apple Music playlist when prompted.

Environment Variables:
- SPOTIFY_CLIENT_ID: Your Spotify API client ID
- SPOTIFY_CLIENT_SECRET: Your Spotify API client secret
- APPLE_MUSIC_KEY_ID: Your Apple Music API key ID
- APPLE_MUSIC_TEAM_ID: Your Apple Music team ID
- APPLE_MUSIC_SECRET_KEY: Your Apple Music secret key (private key)
- APPLE_MUSIC_USER_TOKEN: Your Apple Music user token

Note: This script respects API rate limits and includes error handling. However, be mindful of
your API usage and any changes in the APIs' terms of service.
"""

import os
import sys
import time
from typing import List, Tuple, Optional, Dict, Any

import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import requests
import jwt
from tqdm import tqdm
from ratelimit import limits, sleep_and_retry

# Type aliases
SpotifyTrack = Tuple[str, str]  # (track_name, artist_name)
AppleMusicTrackID = str

# API credentials
SPOTIFY_CLIENT_ID: str = os.getenv('SPOTIFY_CLIENT_ID', '')
SPOTIFY_CLIENT_SECRET: str = os.getenv('SPOTIFY_CLIENT_SECRET', '')
APPLE_MUSIC_KEY_ID: str = os.getenv('APPLE_MUSIC_KEY_ID', '')
APPLE_MUSIC_TEAM_ID: str = os.getenv('APPLE_MUSIC_TEAM_ID', '')
APPLE_MUSIC_SECRET_KEY: str = os.getenv('APPLE_MUSIC_SECRET_KEY', '')
APPLE_MUSIC_USER_TOKEN: str = os.getenv('APPLE_MUSIC_USER_TOKEN', '')

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
        print("Please set all required environment variables.")
        sys.exit(1)

@sleep_and_retry
@limits(calls=1, period=1)  # Limit to 1 call per second
def get_spotify_tracks(playlist_id: str) -> List[SpotifyTrack]:
    """
    Fetch all tracks from a Spotify playlist.
    
    Args:
        playlist_id (str): The Spotify playlist ID.
    
    Returns:
        List[SpotifyTrack]: A list of tuples containing track names and artist names.
    
    Raises:
        SystemExit: If there's an error accessing the Spotify API.
    """
    try:
        client_credentials_manager = SpotifyClientCredentials(
            client_id=SPOTIFY_CLIENT_ID,
            client_secret=SPOTIFY_CLIENT_SECRET
        )
        sp = spotipy.Spotify(client_credentials_manager=client_credentials_manager)
        
        results = sp.playlist_tracks(playlist_id)
        tracks = results['items']
        while results['next']:
            results = sp.next(results)
            tracks.extend(results['items'])
        
        return [(track['track']['name'], track['track']['artists'][0]['name']) for track in tracks if track['track']]
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
        headers = {
            'alg': 'ES256',
            'kid': APPLE_MUSIC_KEY_ID
        }
        payload = {
            'iss': APPLE_MUSIC_TEAM_ID,
            'iat': int(time.time()),
            'exp': int(time.time()) + 15777000
        }
        
        token = jwt.encode(payload, APPLE_MUSIC_SECRET_KEY, algorithm='ES256', headers=headers)
        return token
    except jwt.PyJWTError as e:
        print(f"Error creating Apple Music token: {e}")
        sys.exit(1)

@sleep_and_retry
@limits(calls=20, period=60)  # Limit to 20 calls per minute
def search_apple_music(track_name: str, artist_name: str, token: str) -> Optional[AppleMusicTrackID]:
    """
    Search for a track on Apple Music.
    
    Args:
        track_name (str): The name of the track to search for.
        artist_name (str): The name of the artist.
        token (str): The Apple Music API token.
    
    Returns:
        Optional[AppleMusicTrackID]: The Apple Music track ID if found, None otherwise.
    """
    url = "https://api.music.apple.com/v1/catalog/us/search"
    headers = {
        'Authorization': f'Bearer {token}'
    }
    params = {
        'term': f"{track_name} {artist_name}",
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

def convert_playlist(spotify_playlist_id: str, new_playlist_name: str) -> Tuple[Dict[str, Any], int, int]:
    """
    Convert a Spotify playlist to an Apple Music playlist.
    
    Args:
        spotify_playlist_id (str): The ID of the Spotify playlist to convert.
        new_playlist_name (str): The name for the new Apple Music playlist.
    
    Returns:
        Tuple[Dict[str, Any], int, int]: A tuple containing:
            - The JSON response from creating the Apple Music playlist
            - The total number of tracks in the Spotify playlist
            - The number of tracks successfully transferred to Apple Music
    """
    setup_credentials()
    spotify_tracks = get_spotify_tracks(spotify_playlist_id)
    apple_music_token = get_apple_music_token()
    
    apple_music_track_ids: List[AppleMusicTrackID] = []
    not_found_tracks: List[SpotifyTrack] = []
    
    print(f"Converting {len(spotify_tracks)} tracks...")
    for track_name, artist_name in tqdm(spotify_tracks, unit="track"):
        track_id = search_apple_music(track_name, artist_name, apple_music_token)
        if track_id:
            apple_music_track_ids.append(track_id)
        else:
            not_found_tracks.append((track_name, artist_name))
    
    new_playlist = create_apple_music_playlist(new_playlist_name, apple_music_track_ids, apple_music_token)
    
    print("\nTracks not found on Apple Music:")
    for track_name, artist_name in not_found_tracks:
        print(f"- {track_name} by {artist_name}")
    
    return new_playlist, len(spotify_tracks), len(apple_music_track_ids)

def main() -> None:
    """
    Main function to run the Spotify to Apple Music playlist converter.
    """
    print("Spotify to Apple Music Playlist Converter")
    print("----------------------------------------")
    spotify_playlist_id = input("Enter Spotify playlist ID: ")
    new_playlist_name = input("Enter name for new Apple Music playlist: ")
    
    result, total_tracks, transferred_tracks = convert_playlist(spotify_playlist_id, new_playlist_name)
    print(f"\nNew Apple Music playlist created: {result['data'][0]['attributes']['name']}")
    print(f"Transferred {transferred_tracks} out of {total_tracks} tracks")
    print(f"Success rate: {transferred_tracks/total_tracks:.2%}")

if __name__ == "__main__":
    main()

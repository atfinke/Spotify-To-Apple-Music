from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.backends import default_backend
import jwt
import os
import time

# Load environment variables
key_id = os.getenv('APPLE_MUSIC_KEY_ID')
team_id = os.getenv('APPLE_MUSIC_TEAM_ID')
secret_key = os.getenv('APPLE_MUSIC_SECRET_KEY').replace("\\n", "\n")

# Convert the secret key to bytes
secret_key_bytes = secret_key.encode('utf-8')

# Load your private key
private_key = serialization.load_pem_private_key(
    secret_key_bytes,
    password=None,
    backend=default_backend()
)

# Create a JWT token
now = time.time()
payload = {
    'iss': team_id,
    'iat': now,
    'exp': now + 20 * 60,  # Expire in 20 minutes
}
headers = {
    'kid': key_id
}
token = jwt.encode(payload, private_key, algorithm='ES256', headers=headers)

print(token)

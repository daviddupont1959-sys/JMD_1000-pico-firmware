import ujson
import ucryptolib

# 16-byte key (change this to your own random string!)
KEY = b"1P88FNY2Vi0HNf3E"

def _pad(data: str) -> str:
    while len(data) % 16 != 0:
        data += " "
    return data

def save_config(filename: str, config: dict):
    """Encrypt and save config dict to a file"""
    cipher = ucryptolib.aes(KEY, 1)  # AES-128 ECB
    data = _pad(ujson.dumps(config))
    enc = cipher.encrypt(data.encode())
    with open(filename, "wb") as f:
        f.write(enc)

def load_config(filename: str) -> dict:
    """Load and decrypt config dict from a file"""
    cipher = ucryptolib.aes(KEY, 1)
    with open(filename, "rb") as f:
        enc = f.read()
    dec = cipher.decrypt(enc).decode().strip()
    return ujson.loads(dec)

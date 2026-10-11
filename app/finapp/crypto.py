"""Per-file authenticated encryption (AES-256-GCM) for stored statements and receipts."""
import os
import pathlib
import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"FNC1"


def _aes(key: bytes) -> AESGCM:
    if not key or len(key) != 32:
        raise RuntimeError("FINAPP_FILE_KEY missing or not 32 bytes")
    return AESGCM(key)


def encrypt(key: bytes, data: bytes, aad: bytes = b"") -> bytes:
    nonce = secrets.token_bytes(12)
    return MAGIC + nonce + _aes(key).encrypt(nonce, data, aad)


def decrypt(key: bytes, blob: bytes, aad: bytes = b"") -> bytes:
    if blob[:4] != MAGIC:
        raise ValueError("not an encrypted Ledgerling file")
    return _aes(key).decrypt(blob[4:16], blob[16:], aad)


def store(key: bytes, directory: pathlib.Path, data: bytes, suffix: str = "") -> str:
    """Encrypt and write under a random name; returns the relative filename."""
    directory.mkdir(parents=True, exist_ok=True)
    name = secrets.token_hex(16) + suffix + ".enc"
    path = directory / name
    path.write_bytes(encrypt(key, data, name.encode()))
    os.chmod(path, 0o600)
    return name


def load(key: bytes, directory: pathlib.Path, name: str) -> bytes:
    return decrypt(key, (directory / name).read_bytes(), name.encode())

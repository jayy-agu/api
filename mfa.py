"""
MFA (TOTP) support: secret generation, encryption at rest, QR provisioning,
code verification, and single-use recovery codes.

The raw TOTP secret is never stored in plaintext, and the raw recovery codes
are never stored at all (only their hashes) -- matching the "never log/store
secrets in plaintext" requirement. Nothing here ever touches a real
fingerprint/face biometric; that's WebAuthn's job (Phase 3), which is a
completely separate, unrelated kind of "factor".
"""
import os
import base64
import secrets
import pyotp
import qrcode
import io
from cryptography.fernet import Fernet, InvalidToken
from dotenv import load_dotenv

from security import hash_password, verify_password

load_dotenv()

_MFA_KEY = os.environ.get("MFA_ENCRYPTION_KEY")
if not _MFA_KEY:
    raise RuntimeError(
        "MFA_ENCRYPTION_KEY is not set. Generate one with:\n"
        "  python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"\n"
        "and put it in your .env file."
    )
_fernet = Fernet(_MFA_KEY.encode() if isinstance(_MFA_KEY, str) else _MFA_KEY)

ISSUER_NAME = "Jerry Agu's Dashboard"
RECOVERY_CODE_COUNT = 8


def generate_totp_secret() -> str:
    """Base32 secret, compatible with any standard TOTP app."""
    return pyotp.random_base32()


def encrypt_secret(secret: str) -> str:
    return _fernet.encrypt(secret.encode("utf-8")).decode("utf-8")


def decrypt_secret(encrypted_secret: str) -> str:
    try:
        return _fernet.decrypt(encrypted_secret.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        # Only happens if MFA_ENCRYPTION_KEY changed or data was corrupted.
        raise ValueError("Could not decrypt MFA secret")


def provisioning_uri(secret: str, account_name: str) -> str:
    return pyotp.totp.TOTP(secret).provisioning_uri(name=account_name, issuer_name=ISSUER_NAME)


def qr_code_data_uri(uri: str) -> str:
    """Render the otpauth:// URI as a PNG and return it as a data: URI the
    frontend can drop straight into an <img src>."""
    img = qrcode.make(uri)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
    return f"data:image/png;base64,{b64}"


def verify_totp_code(secret: str, code: str) -> bool:
    if not code or not code.isdigit():
        return False
    # valid_window=1 tolerates minor clock drift (accepts the previous and
    # next 30-second step too), which is standard practice for TOTP.
    return pyotp.totp.TOTP(secret).verify(code, valid_window=1)


# --- Recovery codes ---
# Stored only as hashes (reusing the existing bcrypt helpers); plaintext is
# shown to the user exactly once, at generation time, and never again.
_CODE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"  # no 0/O/1/I to avoid confusion


def _generate_one_code() -> str:
    part1 = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(5))
    part2 = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(5))
    return f"{part1}-{part2}"


def generate_recovery_codes(count: int = RECOVERY_CODE_COUNT) -> list[str]:
    return [_generate_one_code() for _ in range(count)]


def hash_recovery_code(code: str) -> str:
    return hash_password(code.strip().upper())


def verify_recovery_code(code: str, code_hash: str) -> bool:
    return verify_password(code.strip().upper(), code_hash)
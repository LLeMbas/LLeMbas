"""Secret encryption and password hashing."""

from __future__ import annotations

from lembas.security.passwords import (
    hash_password,
    validate_password,
    verify_password,
)
from lembas.services.crypto import decrypt, encrypt, mask


def test_encrypt_round_trip():
    secret = "sk-proj-abcdef1234567890"
    assert decrypt(encrypt(secret)) == secret


def test_encrypt_is_not_reversible_by_eye():
    secret = "sk-proj-abcdef1234567890"
    assert secret not in encrypt(secret)


def test_encrypt_is_randomised():
    """Fernet includes a random IV, so the same input must not repeat."""
    assert encrypt("same") != encrypt("same")


def test_empty_secret_stays_empty():
    """Endpoints that need no key store nothing, not an encrypted blank."""
    assert encrypt("") == ""
    assert decrypt("") == ""


def test_decrypt_fails_soft_on_garbage():
    """An unreadable value means the secret key changed. The admin UI has to
    stay usable so the key can simply be re-entered."""
    assert decrypt("not-a-valid-token") == ""


def test_mask_keeps_enough_to_identify_but_not_to_use():
    masked = mask("sk-proj-abcdef1234567890")
    assert masked.startswith("sk-")
    assert masked.endswith("7890")
    assert "abcdef" not in masked


def test_mask_hides_short_secrets_entirely():
    assert set(mask("short")) == {"*"}


def test_password_round_trip():
    stored = hash_password("speak-friend")
    assert verify_password("speak-friend", stored)
    assert not verify_password("Speak-Friend", stored)


def test_password_hash_is_salted():
    assert hash_password("same") != hash_password("same")


def test_verify_rejects_a_corrupt_hash_instead_of_raising():
    assert not verify_password("anything", "not-a-hash")


def test_short_passwords_are_rejected():
    assert validate_password("short") is not None
    assert validate_password("long-enough-password") is None

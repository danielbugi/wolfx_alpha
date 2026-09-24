"""Unit tests for backend/auth/security.py — password hashing, JWT tokens, code/secret generation.
Framework-free (no FastAPI, no database), matching mechanism/alerts' testing style.

Run:  python -m pytest backend/auth/tests -q      (from the repo root)
"""
import os
import sys
import time

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "backend"))

os.environ.setdefault("JWT_SECRET", "test-secret-do-not-use-in-prod-0123456789")

import auth.security as security  # noqa: E402


def test_password_hash_roundtrip():
    hashed = security.hash_password("correct horse battery staple")
    assert security.verify_password("correct horse battery staple", hashed)
    assert not security.verify_password("wrong password", hashed)


def test_password_hash_is_salted_differently_each_time():
    a = security.hash_password("same password")
    b = security.hash_password("same password")
    assert a != b
    assert security.verify_password("same password", a)
    assert security.verify_password("same password", b)


def test_verify_password_never_raises_on_malformed_hash():
    assert security.verify_password("anything", "not-a-real-bcrypt-hash") is False


def test_hash_secret_is_deterministic_sha256():
    assert security.hash_secret("abc") == security.hash_secret("abc")
    assert security.hash_secret("abc") != security.hash_secret("abd")
    assert len(security.hash_secret("abc")) == 64  # hex sha256


def test_generate_login_code_shape():
    code = security.generate_login_code()
    assert len(code) == security.CODE_LENGTH
    assert code.isdigit()


def test_generate_opaque_token_is_unique_and_unguessable_length():
    a = security.generate_opaque_token()
    b = security.generate_opaque_token()
    assert a != b
    assert len(a) >= 32


def test_access_token_roundtrip():
    payload = security.AccessTokenPayload(user_id=42, role="owner", email="owner@example.com")
    token = security.create_access_token(payload, ttl_minutes=30)
    decoded = security.decode_access_token(token)
    assert decoded is not None
    assert decoded.user_id == 42
    assert decoded.role == "owner"
    assert decoded.email == "owner@example.com"


def test_access_token_expired_is_rejected():
    payload = security.AccessTokenPayload(user_id=1, role="collaborator", email="c@example.com")
    token = security.create_access_token(payload, ttl_minutes=0)
    time.sleep(1.1)
    assert security.decode_access_token(token) is None


def test_access_token_garbage_is_rejected():
    assert security.decode_access_token("not-a-jwt") is None


def test_access_token_requires_jwt_secret(monkeypatch):
    monkeypatch.delenv("JWT_SECRET", raising=False)
    payload = security.AccessTokenPayload(user_id=1, role="owner", email="a@b.com")
    with pytest.raises(RuntimeError):
        security.create_access_token(payload, ttl_minutes=5)

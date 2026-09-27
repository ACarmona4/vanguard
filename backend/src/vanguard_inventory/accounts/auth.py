"""Account persistence, password hashing, and opaque browser sessions."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from psycopg.rows import dict_row


SESSION_DAYS = 7
RESET_MINUTES = 30

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS app_users (
    id UUID PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    full_name VARCHAR(120) NOT NULL,
    password_hash TEXT NOT NULL,
    password_salt TEXT NOT NULL,
    theme VARCHAR(8) NOT NULL DEFAULT 'light' CHECK (theme IN ('light', 'dark')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS auth_sessions (
    token_hash CHAR(64) PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    csrf_token TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS auth_sessions_user_idx ON auth_sessions (user_id);
CREATE INDEX IF NOT EXISTS auth_sessions_expiry_idx ON auth_sessions (expires_at);
CREATE TABLE IF NOT EXISTS password_reset_tokens (
    token_hash CHAR(64) PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    expires_at TIMESTAMPTZ NOT NULL,
    used_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS password_reset_user_idx ON password_reset_tokens (user_id);
CREATE TABLE IF NOT EXISTS auth_login_attempts (
    identifier_hash CHAR(64) PRIMARY KEY,
    attempts SMALLINT NOT NULL,
    window_started_at TIMESTAMPTZ NOT NULL,
    blocked_until TIMESTAMPTZ
);
"""

PUBLIC_USER_COLUMNS = "id, email, full_name, theme, created_at, updated_at"


def ensure_tables(connection) -> None:
    connection.execute(SCHEMA_SQL)


def normalize_email(email: str) -> str:
    return email.strip().casefold()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _password_hash(password: str, salt: bytes) -> str:
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return base64.urlsafe_b64encode(digest).decode()


def hash_password(password: str) -> tuple[str, str]:
    salt = secrets.token_bytes(16)
    return _password_hash(password, salt), base64.urlsafe_b64encode(salt).decode()


def verify_password(password: str, stored_hash: str, stored_salt: str) -> bool:
    try:
        salt = base64.urlsafe_b64decode(stored_salt.encode())
        candidate = _password_hash(password, salt)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(candidate, stored_hash)


def create_user(connection, *, email: str, full_name: str, password: str) -> dict[str, Any]:
    ensure_tables(connection)
    password_hash, salt = hash_password(password)
    with connection.cursor(row_factory=dict_row) as cursor:
        user = cursor.execute(
            f"""
            INSERT INTO app_users (id, email, full_name, password_hash, password_salt)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING {PUBLIC_USER_COLUMNS}
            """,
            (str(uuid4()), normalize_email(email), full_name.strip(), password_hash, salt),
        ).fetchone()
    connection.commit()
    return user


def authenticate(
    connection, email: str, password: str, *, remote_address: str = "unknown"
) -> dict[str, Any] | None:
    now = datetime.now(timezone.utc)
    attempt_key = _token_hash(f"{normalize_email(email)}|{remote_address}")
    attempt = connection.execute(
        "SELECT attempts, window_started_at, blocked_until FROM auth_login_attempts WHERE identifier_hash = %s",
        (attempt_key,),
    ).fetchone()
    if attempt and attempt["blocked_until"] and attempt["blocked_until"] > now:
        return None
    with connection.cursor(row_factory=dict_row) as cursor:
        row = cursor.execute(
            f"SELECT {PUBLIC_USER_COLUMNS}, password_hash, password_salt FROM app_users WHERE email = %s",
            (normalize_email(email),),
        ).fetchone()
    if not row or not verify_password(password, row["password_hash"], row["password_salt"]):
        if not attempt or attempt["window_started_at"] < now - timedelta(minutes=15):
            attempts = 1
            window_started = now
        else:
            attempts = attempt["attempts"] + 1
            window_started = attempt["window_started_at"]
        blocked_until = now + timedelta(minutes=15) if attempts >= 5 else None
        connection.execute(
            """
            INSERT INTO auth_login_attempts (identifier_hash, attempts, window_started_at, blocked_until)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (identifier_hash) DO UPDATE SET attempts = EXCLUDED.attempts,
                window_started_at = EXCLUDED.window_started_at, blocked_until = EXCLUDED.blocked_until
            """,
            (attempt_key, attempts, window_started, blocked_until),
        )
        connection.commit()
        return None
    connection.execute("DELETE FROM auth_login_attempts WHERE identifier_hash = %s", (attempt_key,))
    connection.commit()
    return {key: row[key] for key in PUBLIC_USER_COLUMNS.split(", ")}


def create_session(connection, user_id: str) -> tuple[str, str, datetime]:
    ensure_tables(connection)
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(24)
    expires_at = datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)
    connection.execute(
        "INSERT INTO auth_sessions (token_hash, user_id, csrf_token, expires_at) VALUES (%s, %s, %s, %s)",
        (_token_hash(token), user_id, csrf, expires_at),
    )
    connection.execute("DELETE FROM auth_sessions WHERE expires_at <= NOW()")
    connection.commit()
    return token, csrf, expires_at


def get_session(connection, token: str | None) -> dict[str, Any] | None:
    if not token:
        return None
    with connection.cursor(row_factory=dict_row) as cursor:
        return cursor.execute(
            f"""
            SELECT {', '.join(f'u.{column}' for column in PUBLIC_USER_COLUMNS.split(', '))},
                   s.csrf_token, s.token_hash, s.expires_at
            FROM auth_sessions s
            JOIN app_users u ON u.id = s.user_id
            WHERE s.token_hash = %s AND s.expires_at > NOW()
            """,
            (_token_hash(token),),
        ).fetchone()


def delete_session(connection, token: str | None) -> None:
    if token:
        connection.execute("DELETE FROM auth_sessions WHERE token_hash = %s", (_token_hash(token),))
        connection.commit()


def update_profile(connection, user_id: str, *, full_name: str, theme: str) -> dict[str, Any]:
    with connection.cursor(row_factory=dict_row) as cursor:
        user = cursor.execute(
            f"""
            UPDATE app_users SET full_name = %s, theme = %s, updated_at = NOW()
            WHERE id = %s RETURNING {PUBLIC_USER_COLUMNS}
            """,
            (full_name.strip(), theme, user_id),
        ).fetchone()
    connection.commit()
    return user


def change_password(
    connection, user_id: str, *, current_password: str, new_password: str, current_token_hash: str
) -> bool:
    row = connection.execute(
        "SELECT password_hash, password_salt FROM app_users WHERE id = %s", (user_id,)
    ).fetchone()
    if not row or not verify_password(current_password, row["password_hash"], row["password_salt"]):
        return False
    password_hash, salt = hash_password(new_password)
    connection.execute(
        "UPDATE app_users SET password_hash = %s, password_salt = %s, updated_at = NOW() WHERE id = %s",
        (password_hash, salt, user_id),
    )
    connection.execute(
        "DELETE FROM auth_sessions WHERE user_id = %s AND token_hash <> %s",
        (user_id, current_token_hash),
    )
    connection.commit()
    return True


def create_reset_token(connection, email: str) -> str | None:
    ensure_tables(connection)
    row = connection.execute("SELECT id FROM app_users WHERE email = %s", (normalize_email(email),)).fetchone()
    if not row:
        return None
    token = secrets.token_urlsafe(32)
    connection.execute("DELETE FROM password_reset_tokens WHERE user_id = %s", (row["id"],))
    connection.execute(
        "INSERT INTO password_reset_tokens (token_hash, user_id, expires_at) VALUES (%s, %s, %s)",
        (_token_hash(token), row["id"], datetime.now(timezone.utc) + timedelta(minutes=RESET_MINUTES)),
    )
    connection.commit()
    return token


def reset_password(connection, token: str, new_password: str) -> bool:
    row = connection.execute(
        """
        SELECT user_id FROM password_reset_tokens
        WHERE token_hash = %s AND used_at IS NULL AND expires_at > NOW()
        FOR UPDATE
        """,
        (_token_hash(token),),
    ).fetchone()
    if not row:
        return False
    password_hash, salt = hash_password(new_password)
    connection.execute(
        "UPDATE app_users SET password_hash = %s, password_salt = %s, updated_at = NOW() WHERE id = %s",
        (password_hash, salt, row["user_id"]),
    )
    connection.execute(
        "UPDATE password_reset_tokens SET used_at = NOW() WHERE token_hash = %s",
        (_token_hash(token),),
    )
    connection.execute("DELETE FROM auth_sessions WHERE user_id = %s", (row["user_id"],))
    connection.commit()
    return True

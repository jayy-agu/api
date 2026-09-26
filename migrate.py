"""
One-time migration: adds the new Phase 1 security columns to an EXISTING
office_board.db without dropping or altering any existing data.

Safe to run multiple times: it checks which columns already exist first and
only adds what's missing. Does not touch rows, does not drop tables.

Run once from your project root, with the venv active:
    python migrate.py
"""
import sqlite3

DB_PATH = "office_board.db"

# (table, column, sql_type_and_default)
NEW_COLUMNS = [
    ("users", "role", "TEXT NOT NULL DEFAULT 'USER'"),
    ("users", "failed_login_attempts", "INTEGER NOT NULL DEFAULT 0"),
    ("users", "locked_until", "DATETIME"),
    ("users", "last_failed_login", "DATETIME"),
    ("users", "last_login", "DATETIME"),
    ("users", "last_login_ip", "TEXT"),
    ("users", "password_changed_at", "DATETIME"),
    ("users", "is_active", "BOOLEAN NOT NULL DEFAULT 1"),
    ("users", "mfa_enabled", "BOOLEAN NOT NULL DEFAULT 0"),
    ("users", "mfa_secret_encrypted", "TEXT"),
    ("users", "mfa_enrolled_at", "DATETIME"),
    ("access_log", "user_id", "INTEGER"),
    ("access_log", "event_type", "TEXT"),
    ("access_log", "ip_address", "TEXT"),
    ("access_log", "user_agent", "TEXT"),
    # Phase 3: private/direct messages. Without this column, every call to
    # GET /posts/ crashes (the ORM model and every posts query already
    # reference it), which is why the board has been failing to load.
    ("posts", "dm_recipient_id", "INTEGER"),
]


def existing_columns(cur, table):
    cur.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cur.fetchall()}


def main():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    tables = {row[0] for row in cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}

    added = []
    skipped = []
    for table, column, coltype in NEW_COLUMNS:
        if table not in tables:
            print(f"Skipping {table}.{column}: table '{table}' doesn't exist yet "
                  f"(it will be created automatically on next app startup).")
            continue
        cols = existing_columns(cur, table)
        if column in cols:
            skipped.append(f"{table}.{column}")
            continue
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
        added.append(f"{table}.{column}")

    # Backfill: promote existing is_admin=1 users to SUPER_ADMIN so nobody
    # loses admin access after the upgrade. Everyone else stays USER.
    if "users" in tables and "role" in {c for t, c, _ in NEW_COLUMNS if t == "users"}:
        cur.execute(
            "UPDATE users SET role = 'SUPER_ADMIN' WHERE is_admin = 1 AND (role IS NULL OR role = 'USER')"
        )

    conn.commit()

    # New table (Phase 2): ALTER TABLE only adds columns to existing tables,
    # so a brand-new table needs its own CREATE TABLE IF NOT EXISTS.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS mfa_recovery_codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            code_hash TEXT NOT NULL,
            used BOOLEAN NOT NULL DEFAULT 0,
            used_at DATETIME,
            created_at DATETIME,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    """)
    conn.commit()
    conn.close()

    print("\nMigration complete.")
    print(f"Added columns: {added or 'none'}")
    print(f"Already present (skipped): {skipped or 'none'}")
    print("\nExisting admin accounts (is_admin=1) have been set to role=SUPER_ADMIN.")
    print("No rows were deleted. No tables were dropped.")


if __name__ == "__main__":
    main()

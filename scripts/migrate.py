"""Apply checked-in Verity migrations to a server-side DATABASE_URL."""

import argparse
import hashlib
import os
from pathlib import Path

import psycopg

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


def migrate(db: psycopg.Connection, directory: Path = MIGRATIONS) -> list[str]:
    applied = []
    with db.transaction():
        db.execute("SELECT pg_advisory_xact_lock(hashtext('verity.schema.migrations'))")
        db.execute("CREATE SCHEMA IF NOT EXISTS verity_migrations")
        db.execute("REVOKE ALL ON SCHEMA verity_migrations FROM PUBLIC, anon, authenticated")
        db.execute("""CREATE TABLE IF NOT EXISTS verity_migrations.applied (
                         filename text PRIMARY KEY, sha256 text NOT NULL, applied_at timestamptz DEFAULT now()
                     )""")
        db.execute("REVOKE ALL ON verity_migrations.applied FROM PUBLIC, anon, authenticated")
        recorded = dict(db.execute("SELECT filename, sha256 FROM verity_migrations.applied").fetchall())
        files = sorted(directory.glob("*.sql"))
        if not files or recorded.keys() - {path.name for path in files}:
            raise ValueError("The checked-in migration set is incomplete.")
        for file in files:
            content = file.read_bytes().decode("utf-8").replace("\r\n", "\n")
            digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
            if file.name in recorded:
                if recorded[file.name] != digest:
                    raise ValueError(f"Migration {file.name} differs from the applied version.")
                continue
            # The SQL files stay directly runnable. The runner owns the transaction for both SQL and its ledger.
            first, *lines, last = content.strip().splitlines()
            if first != "BEGIN;" or last != "COMMIT;":
                raise ValueError(f"Migration {file.name} must have a BEGIN/COMMIT envelope.")
            db.execute("\n".join(lines), prepare=False)
            db.execute("INSERT INTO verity_migrations.applied (filename, sha256) VALUES (%s, %s)",
                       (file.name, digest))
            applied.append(file.name)
    return applied


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    url = os.environ.get("DATABASE_URL")
    if not url:
        parser.exit(1, "Set DATABASE_URL in the process environment.\n")
    try:
        with psycopg.connect(url, connect_timeout=8, autocommit=True) as db:
            applied = migrate(db)
    except psycopg.Error as exc:
        parser.exit(1, f"Migration failed ({type(exc).__name__}, SQLSTATE {exc.sqlstate or 'unavailable'}).\n")
    except (ValueError, OSError) as exc:
        parser.exit(1, f"Migration files could not be verified ({type(exc).__name__}).\n")
    print("Applied " + ", ".join(applied) if applied else "Database is up to date.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""
Run this ONCE on your existing database before starting app.py.
Adds the scheduled/recurring post columns to the products table.

Usage:
    python migrate.py
"""
import sqlite3, os

DB_PATH = os.path.join(os.path.dirname(__file__), 'instance', 'agristore.db')

if not os.path.exists(DB_PATH):
    print("No existing database found — will be created fresh on first run.")
    exit(0)

conn = sqlite3.connect(DB_PATH)
c    = conn.cursor()

new_columns = [
    ("post_status",       "TEXT DEFAULT 'none'"),
    ("post_tone",         "TEXT DEFAULT 'friendly'"),
    ("scheduled_post_at", "DATETIME"),
    ("recurring_enabled", "INTEGER DEFAULT 0"),
    ("recurring_days",    "INTEGER DEFAULT 7"),
    ("last_posted_at",    "DATETIME"),
]

print(f"Migrating: {DB_PATH}")
ok = skip = 0
for col, definition in new_columns:
    try:
        c.execute(f"ALTER TABLE products ADD COLUMN {col} {definition}")
        print(f"  ✅ Added products.{col}")
        ok += 1
    except sqlite3.OperationalError as e:
        if 'duplicate column' in str(e).lower():
            print(f"  ⏭️  products.{col} already exists — skipped")
            skip += 1
        else:
            print(f"  ⚠️  {e}")

conn.commit()
conn.close()
print(f"\nDone — {ok} columns added, {skip} already existed.")
print("You can now run: python app.py")

#!/usr/bin/env python3
"""Set key=value pairs in the 3x-ui settings table. Prints 'changed' if anything changed."""
import sqlite3
import sys

DB = "/etc/x-ui/x-ui.db"

con = sqlite3.connect(DB, timeout=10)
changed = False
for arg in sys.argv[1:]:
    key, value = arg.split("=", 1)
    rows = con.execute("SELECT id, value FROM settings WHERE key = ?", (key,)).fetchall()
    if not rows:
        con.execute("INSERT INTO settings (key, value) VALUES (?, ?)", (key, value))
        changed = True
    elif any(v != value for _, v in rows):
        con.execute("UPDATE settings SET value = ? WHERE key = ?", (value, key))
        changed = True
con.commit()
con.close()
print("changed" if changed else "ok")

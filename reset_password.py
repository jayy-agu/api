"""
One-off script to reset a user's password in office_board.db.
Run once from your project root: python reset_password.py
Then delete this file.
"""

from database import get_db
import models
from security import hash_password

HANDLE_TO_RESET = "ada"          # change if needed
NEW_PASSWORD = "changeme123"     # <-- set your new password here (4+ chars)

db = next(get_db())

user = db.query(models.User).filter(models.User.handle == HANDLE_TO_RESET).first()

if not user:
    print(f"No user found with handle '{HANDLE_TO_RESET}'")
else:
    user.password_hash = hash_password(NEW_PASSWORD)
    db.commit()
    print(f"Password for '{user.handle}' ({user.name}) has been reset.")
    print(f"New password: {NEW_PASSWORD}")
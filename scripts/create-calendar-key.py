"""Create a Fernet key for Docker secrets without displaying it."""
import base64
import os
from pathlib import Path
import secrets

folder = Path(__file__).resolve().parents[1] / 'secrets'
folder.mkdir(mode=0o700, exist_ok=True)
path = folder / 'calendar_key'
# Exclusive creation protects existing encrypted subscriptions from key replacement.
try:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
except FileExistsError:
    raise SystemExit('Calendar key already exists; preserved')
with os.fdopen(fd, 'wb') as stream:
    stream.write(base64.urlsafe_b64encode(secrets.token_bytes(32)) + b'\n')
    stream.flush()
    os.fsync(stream.fileno())
# The unprivileged container must read the bind-mounted Docker secret. The private
# parent directory prevents other host users from traversing to this file.
folder.chmod(0o700)
path.chmod(0o444)
print('Calendar key created')

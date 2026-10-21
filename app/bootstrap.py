"""Generate local secrets inside Docker without displaying them."""
import os
import base64
from pathlib import Path
import secrets


def main():
    directory = Path("/bootstrap")
    directory.mkdir(exist_ok=True)
    directory.chmod(0o700)
    for name in ("db_password",):
        path = directory / name
        if not path.exists():
            # Bind-backed Compose secrets keep host file mode. Directory 0700
            # protects host access; mounted files must be readable by UID 10001.
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            with os.fdopen(fd, "w") as stream:
                stream.write(secrets.token_urlsafe(40) + "\n")
    # Nonempty inert placeholder, never used while Telegram is disabled.
    placeholder = directory / "telegram_token"
    if not placeholder.exists():
        fd = os.open(placeholder, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        with os.fdopen(fd, "w") as stream:
            stream.write("DISABLED\n")
    calendar_key = directory / 'calendar_key'
    if not calendar_key.exists():
        fd = os.open(calendar_key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(base64.urlsafe_b64encode(secrets.token_bytes(32)) + b'\n')
            stream.flush()
            os.fsync(stream.fileno())
    print("Local secret files prepared; values were not displayed.")


if __name__ == "__main__":
    main()

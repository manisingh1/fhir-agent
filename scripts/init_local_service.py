"""Create an ignored development grant key without printing or overwriting it."""
import os
import secrets
from pathlib import Path

if __name__ == '__main__':
    directory = Path('.runtime')
    directory.mkdir(mode=0o700, exist_ok=True)
    path = directory / 'grant-key'
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        print('Existing local grant key retained.')
    else:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(secrets.token_bytes(32))
        print('Created local development grant key in ignored .runtime/.')

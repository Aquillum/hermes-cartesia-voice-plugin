"""Local, user-wide Cartesia favorites. No credentials or vendor requests."""
import json
import os
import re
import secrets
import sys
from contextlib import contextmanager
from pathlib import Path

from hermes_constants import get_default_hermes_root

FIELDS = ('name', 'language', 'gender', 'voice_id')


def validate_favorites(value):
    if not isinstance(value, list) or len(value) > 5:
        raise ValueError('Favorites must contain at most five slots.')
    result, seen = [], set()
    for slot in value:
        if not isinstance(slot, dict) or set(slot) != set(FIELDS):
            raise ValueError('Each slot requires name, language, gender and voice_id.')
        if any(not isinstance(slot[key], str) for key in FIELDS):
            raise ValueError('Favorite fields must be strings.')
        if any(slot.values()):
            if (not slot['name'].strip() or len(slot['name']) > 100
                    or len(slot['language']) > 35
                    or not re.fullmatch(r'[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*', slot['language'])
                    or slot['gender'] not in ('male', 'female')
                    or not re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', slot['voice_id'])
                    or any(ord(c) < 32 for c in slot['name'])):
                raise ValueError('Invalid or incomplete favorite slot.')
            key = slot['voice_id'].lower()
            if key in seen:
                raise ValueError('Duplicate voice ID.')
            seen.add(key)
        result.append(dict(slot))
    return result + [dict.fromkeys(FIELDS, '') for _ in range(5 - len(result))]


def _require_supported_platform():
    if (sys.platform not in ('linux', 'darwin')
            or any(not hasattr(os, flag) for flag in ('O_DIRECTORY', 'O_NOFOLLOW', 'O_NONBLOCK'))
            or any(fn not in os.supports_dir_fd for fn in (os.open, os.mkdir, os.unlink))):
        raise ValueError('Cartesia favorites management requires Linux/macOS.')


@contextmanager
def _directory():
    _require_supported_platform()
    # Traverse with directory descriptors: neither existing nor raced symlinks
    # can redirect a read/write outside this fixed, supported local-user path.
    root = Path(get_default_hermes_root()).absolute()
    fd = os.open(root.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in (*root.parts[1:], 'plugin-data', 'cartesia-voice'):
            try:
                os.mkdir(part, 0o700, dir_fd=fd)
            except FileExistsError:
                pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def _read(fd):
    try:
        handle = os.open('favorites.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    except FileNotFoundError:
        return validate_favorites([])
    with os.fdopen(handle, 'rb') as stream:
        import stat
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('Favorites must be a regular file.')
        data = stream.read(16385)
    if len(data) > 16384:
        raise ValueError('Favorites file exceeds size limit.')
    return validate_favorites(json.loads(data))


def read_favorites():
    _require_supported_platform()
    try:
        with _directory() as fd:
            return _read(fd)
    except (OSError, ValueError, UnicodeError):
        raise ValueError('Cartesia favorites unavailable or invalid.') from None


def write_favorites(favorites):
    _require_supported_platform()
    import fcntl
    import stat
    import time
    value = validate_favorites(favorites)
    temporary = None
    try:
        with _directory() as fd:
            lock = os.open('favorites.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=fd)
            try:
                if not stat.S_ISREG(os.fstat(lock).st_mode):
                    raise ValueError('Invalid favorites lock.')
                os.fchmod(lock, 0o600)
                deadline = time.monotonic() + 2
                while True:
                    try:
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        if time.monotonic() >= deadline:
                            raise ValueError('Favorites are busy; retry.')
                        time.sleep(0.02)
                _read(fd)  # Fail closed: never replace damaged or symlinked state.
                temporary = '.favorites-' + secrets.token_hex(12)
                out = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
                try:
                    with os.fdopen(out, 'wb') as stream:
                        stream.write(json.dumps(value, ensure_ascii=False).encode('utf-8'))
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(temporary, 'favorites.json', src_dir_fd=fd, dst_dir_fd=fd)
                    os.fsync(fd)
                finally:
                    if temporary is not None:
                        try:
                            os.unlink(temporary, dir_fd=fd)
                        except FileNotFoundError:
                            pass
            finally:
                os.close(lock)
    except (OSError, ValueError, UnicodeError):
        raise ValueError('Cartesia favorites could not be saved; check local state.') from None
    return value

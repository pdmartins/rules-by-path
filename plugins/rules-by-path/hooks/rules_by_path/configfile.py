"""Getting one `config.json` off disk, from a directory that may belong to a
cloned repository.

A module of its own rather than another function in `config.py`: `config.py`
is at the 400-line ceiling this package holds every module to, and this is a
concern that stands apart from validating a layer's *contents* — it is the
sibling of `rules.read_rule_file`, and it opens its file the same paranoid way,
because a project's `.claude/rules-by-path/config.json` is repository data
exactly like a rule file is.
"""

import json
import os
import stat

from .constants import CONFIG_FILE_NAME, MAX_CONFIG_BYTES, warn


def config_path_for(scope_dir):
    return os.path.join(scope_dir, CONFIG_FILE_NAME)


def read_config_file(path):
    """Parse one config file, or None when there is nothing usable there.

    Opened the way a rule file is (`rules.read_rule_file`): no symlink, regular
    file only, bounded read. A project's `.claude/rules-by-path/` is repository
    data, so `config.json` there can be a link to `/etc/shadow` or a gigabyte of
    JSON, and neither may reach a parser."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        return None  # by far the common case: no config at this layer
    except OSError as exc:
        warn(f"cannot open {path}: {exc}")
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            warn(f"{path} is not a regular file; ignored")
            return None
        with os.fdopen(fd, encoding="utf-8", errors="replace") as handle:
            fd = None  # fdopen owns it now
            text = handle.read(MAX_CONFIG_BYTES + 1)
    except Exception as exc:
        warn(f"cannot read {path}: {exc}")
        return None
    finally:
        if fd is not None:
            os.close(fd)
    if len(text) > MAX_CONFIG_BYTES:
        warn(f"{path} is larger than {MAX_CONFIG_BYTES} bytes; ignored")
        return None
    try:
        loaded = json.loads(text)
    except Exception as exc:
        # Broader than ValueError on purpose. 32KB is room for sixteen thousand
        # nested arrays, and the decoder answers that with RecursionError, which
        # is not a ValueError and would leave this function by raising — from a
        # file that arrived with a repository, on the path that also decides an
        # `enforce: deny`. What a config file may cost is itself, nothing more.
        warn(f"{path} is not valid JSON ({exc}); ignored")
        return None
    if not isinstance(loaded, dict):
        warn(f"{path} does not hold a JSON object; ignored")
        return None
    return loaded

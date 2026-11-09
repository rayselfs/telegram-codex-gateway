"""Deployment-neutral gateway package."""
from pathlib import Path
import os
import stat

# The existing Windows deployment runs Python 3.11; keep its junction checks real.
if not hasattr(Path, 'is_junction'):
    def _is_junction(path):
        try:
            return getattr(os.lstat(path), 'st_reparse_tag', None) == getattr(stat, 'IO_REPARSE_TAG_MOUNT_POINT', -1)
        except (OSError, ValueError):
            return False
    Path.is_junction = _is_junction

"""Select native implementations without importing another platform's APIs."""
import importlib
import sys

def current(platform=None):
    name = {'win32': 'windows', 'darwin': 'macos'}.get(platform or sys.platform)
    if name is None:
        raise RuntimeError('Gateway supports only Windows and macOS')
    return importlib.import_module(f'gateway.platforms.{name}')

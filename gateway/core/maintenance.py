"""Persistent local hold for service startup, scheduling and Telegram requests."""
from gateway.core.bootstrap import ROOT


def held(root=None):
    return ((ROOT if root is None else root) / 'profile/maintenance.request').exists()


def require_live(root=None):
    if held(root):
        raise PermissionError('Maintenance hold is active')

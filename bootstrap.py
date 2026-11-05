"""Process-local environment setup; run before importing any Hermes modules."""
import os
from pathlib import Path
import sys

ROOT = Path(os.environ.get('TELEGRAM_CODEX_ROOT','D:/CodexTelegram')).resolve()
PYTHON = ROOT / 'runtime/gateway-venv/Scripts/python.exe'
CODEX = Path(os.environ.get('TELEGRAM_CODEX_BINARY',str(ROOT / 'runtime/package/vendor/x86_64-pc-windows-msvc/bin/codex.exe')))


def isolate(test=False):
    if not ROOT.is_dir():
        raise RuntimeError('D drive installation is unavailable')
    home = ROOT / ('test-state/profile' if test else 'profile')
    tmp = ROOT / ('test-state/temp' if test else 'temp')
    for path in (home, home / 'codex-home', tmp):
        path.mkdir(parents=True, exist_ok=True)
    allowed = {'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT', 'PATH', 'USERPROFILE',
               'HOME', 'HOMEDRIVE', 'HOMEPATH', 'APPDATA', 'LOCALAPPDATA',
               'PROGRAMFILES', 'PROGRAMFILES(X86)', 'PROGRAMDATA', 'USERNAME',
               'TELEGRAM_CODEX_ROOT', 'OS', 'PROCESSOR_ARCHITECTURE', 'NUMBER_OF_PROCESSORS'}
    clean = {k: v for k, v in os.environ.items() if k.upper() in allowed}
    clean.update(CODEX_HOME=str(home / 'codex-home'),
                 CODEX_SQLITE_HOME=str(home / 'codex-home'), TEMP=str(tmp), TMP=str(tmp),
                 PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1',
                 XDG_CACHE_HOME=str(home / 'cache'), UV_CACHE_DIR=str(home / 'cache/uv'),
                 PIP_CACHE_DIR=str(home / 'cache/pip'), npm_config_cache=str(home / 'cache/npm'))
    os.environ.clear()
    os.environ.update(clean)
    sys.dont_write_bytecode = True
    os.chdir(ROOT / 'bridge')
    return home


def child_environment():
    allowed={'TELEGRAM_CODEX_ROOT','SYSTEMROOT','WINDIR','COMSPEC','PATHEXT','PATH','USERPROFILE','HOME','HOMEDRIVE','HOMEPATH','APPDATA','LOCALAPPDATA','PROGRAMFILES','PROGRAMFILES(X86)','PROGRAMDATA','USERNAME','OS','PROCESSOR_ARCHITECTURE','NUMBER_OF_PROCESSORS','TEMP','TMP','PYTHONUTF8'}
    return {k:v for k,v in os.environ.items() if k.upper() in allowed}

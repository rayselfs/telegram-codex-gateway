"""Process-local environment setup; run before importing any Hermes modules."""
import os
from pathlib import Path
import sys

from gateway.core.deployment import load

CONFIG_PATH = os.environ.get('TELEGRAM_CODEX_CONFIG')
if '--config' in sys.argv:
    i = sys.argv.index('--config')
    if i + 1 >= len(sys.argv): raise ValueError('--config requires an absolute path')
    CONFIG_PATH = sys.argv[i + 1]
    del sys.argv[i:i + 2]
CONFIG = load(Path(CONFIG_PATH)) if CONFIG_PATH else None
ROOT = Path(CONFIG['data_root']) if CONFIG else (Path(os.environ['TELEGRAM_CODEX_ROOT']) if os.environ.get('TELEGRAM_CODEX_ROOT') else None)
if ROOT is None:
    raise RuntimeError('Deployment configuration required; run init or pass --config')
ROOT = ROOT.resolve()
CODE_ROOT = Path(CONFIG['code_root']) if CONFIG else Path(__file__).resolve().parent
PYTHON = Path(CONFIG['python']) if CONFIG else Path(sys.executable)
CODEX = Path(CONFIG['codex']) if CONFIG else Path(os.environ.get('TELEGRAM_CODEX_BINARY', str(ROOT/'runtime/package/vendor/x86_64-pc-windows-msvc/bin/codex.exe')))


def isolate(test=False):
    if not ROOT.is_dir():
        raise RuntimeError('Configured data directory is unavailable')
    home = ROOT / ('test-state/profile' if test else 'profile')
    tmp = ROOT / ('test-state/temp' if test else 'temp')
    for path in (home, home / 'codex-home', tmp):
        path.mkdir(parents=True, exist_ok=True)
    allowed = {'TELEGRAM_CODEX_CONFIG', 'TELEGRAM_CODEX_ROOT', 'TELEGRAM_CODEX_BINARY', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT', 'PATH', 'USERPROFILE',
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
    if CONFIG_PATH: os.environ['TELEGRAM_CODEX_CONFIG'] = str(Path(CONFIG_PATH).absolute())
    os.environ['TELEGRAM_CODEX_ROOT'] = str(ROOT)
    os.chdir(CODE_ROOT)
    return home


def child_environment():
    allowed={'TELEGRAM_CODEX_ROOT','TELEGRAM_CODEX_CONFIG','TELEGRAM_CODEX_ROOT','TELEGRAM_CODEX_BINARY','SYSTEMROOT','WINDIR','COMSPEC','PATHEXT','PATH','USERPROFILE','HOME','HOMEDRIVE','HOMEPATH','APPDATA','LOCALAPPDATA','PROGRAMFILES','PROGRAMFILES(X86)','PROGRAMDATA','USERNAME','OS','PROCESSOR_ARCHITECTURE','NUMBER_OF_PROCESSORS','TEMP','TMP','PYTHONUTF8'}
    env={k:v for k,v in os.environ.items() if k.upper() in allowed}
    env['TELEGRAM_CODEX_ROOT']=str(ROOT)
    if CONFIG_PATH:env['TELEGRAM_CODEX_CONFIG']=str(Path(CONFIG_PATH).absolute())
    return env

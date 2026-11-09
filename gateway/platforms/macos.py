"""Native macOS mechanisms. Native acceptance is required before release."""
import ctypes
import fcntl
import hashlib
import os
from pathlib import Path
import signal
import threading
import uuid

class SingleInstance:
    def __init__(self, path):
        self.file = Path(path).open('a+b')
        try:
            fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise RuntimeError('This gateway is already running') from None

    def close(self):
        self.file.close()

def process_options():
    return {'start_new_session': True}

class ChildJob:
    def __init__(self, process):
        if os.getpgid(process.pid) != process.pid:
            raise RuntimeError('Child must start in its own process group')
        self.group = process.pid

    def close(self):
        if self.group is not None:
            try:
                os.killpg(self.group, signal.SIGKILL)
            except ProcessLookupError:
                pass
            self.group = None

def opened_path(source):
    # F_GETPATH returns the path of the open descriptor on Darwin.
    raw = fcntl.fcntl(source.fileno(), 50, bytes(1024))
    path = Path(os.fsdecode(raw.split(b'\0', 1)[0]))
    if not path.is_absolute() or not os.path.samestat(os.fstat(source.fileno()), path.stat()):
        raise ValueError('Opened file identity changed')
    return path

_keychain_lock = threading.Lock()

def _keychain(account, value=None, *, delete=False):
    security = ctypes.CDLL('/System/Library/Frameworks/Security.framework/Security')
    cf = ctypes.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
    ptr, uint, char = ctypes.c_void_p, ctypes.c_uint32, ctypes.c_char_p
    service = b'org.telegram-codex.gateway'
    account = account.encode('ascii')
    security.SecKeychainGetUserInteractionAllowed.argtypes = [ctypes.POINTER(ctypes.c_ubyte)]
    security.SecKeychainSetUserInteractionAllowed.argtypes = [ctypes.c_ubyte]
    security.SecKeychainAddGenericPassword.argtypes = [ptr, uint, char, uint, char, uint, ptr, ctypes.POINTER(ptr)]
    security.SecKeychainFindGenericPassword.argtypes = [ptr, uint, char, uint, char, ctypes.POINTER(uint), ctypes.POINTER(ptr), ctypes.POINTER(ptr)]
    security.SecKeychainItemFreeContent.argtypes = [ptr, ptr]
    security.SecKeychainItemDelete.argtypes = [ptr]
    cf.CFRelease.argtypes = [ptr]
    with _keychain_lock:
        previous = ctypes.c_ubyte()
        if security.SecKeychainGetUserInteractionAllowed(ctypes.byref(previous)) != 0:
            raise RuntimeError('Keychain access blocked')
        if security.SecKeychainSetUserInteractionAllowed(False) != 0:
            raise RuntimeError('Keychain access blocked')
        item, data, size = ptr(), ptr(), uint()
        try:
            if value is not None:
                buffer = ctypes.create_string_buffer(value)
                status = security.SecKeychainAddGenericPassword(None, len(service), service,
                    len(account), account, len(value), buffer, ctypes.byref(item))
                if status != 0:
                    raise RuntimeError('Keychain storage blocked')
                return None
            status = security.SecKeychainFindGenericPassword(None, len(service), service,
                len(account), account, ctypes.byref(size), ctypes.byref(data), ctypes.byref(item))
            if status != 0:
                raise RuntimeError('Keychain access blocked')
            if delete:
                if security.SecKeychainItemDelete(item) != 0:
                    raise RuntimeError('Keychain removal blocked')
                return None
            return ctypes.string_at(data, size.value)
        finally:
            if data:
                security.SecKeychainItemFreeContent(None, data)
            if item:
                cf.CFRelease(item)
            security.SecKeychainSetUserInteractionAllowed(previous)

def crypt(data, entropy, encrypt):
    prefix = hashlib.sha256(entropy).hexdigest() + ':'
    if encrypt:
        account = prefix + uuid.uuid4().hex
        _keychain(account, data)
        return ('keychain:' + account).encode('ascii')
    text = data.decode('ascii')
    if not text.startswith('keychain:' + prefix) or len(text) != 106:
        raise RuntimeError('Credential reference does not match owner')
    return _keychain(text[9:])

def delete_secret(reference, entropy):
    prefix = 'keychain:' + hashlib.sha256(entropy).hexdigest() + ':'
    text = reference.decode('ascii')
    if not text.startswith(prefix) or len(text) != 106:
        raise RuntimeError('Credential reference does not match owner')
    _keychain(text[9:], delete=True)

def decrypt_token(path):
    return crypt(Path(path).read_bytes(), b'', False).decode('utf-8')


class BotInstance(SingleInstance):
    def __init__(self,token,*,lock_root=None):
        bot=token.partition(':')[0]
        if not bot.isascii() or not bot.isdigit() or ':' not in token:raise ValueError('Invalid bot identity')
        root=Path(lock_root) if lock_root is not None else Path.home()/'Library/Caches/telegram-codex-gateway/bots'
        for part in (root,*root.parents):
            if part.is_symlink():raise RuntimeError('Bot lock directory is redirected')
        root.mkdir(parents=True,exist_ok=True)
        super().__init__(root/(hashlib.sha256(bot.encode()).hexdigest()+'.lock'))


def install_background(jobs):
    import plistlib,subprocess
    root=Path.home()/'Library/LaunchAgents'
    for part in (root,*root.parents):
        if part.is_symlink():raise RuntimeError('LaunchAgent directory is redirected')
    root.mkdir(parents=True,exist_ok=True)
    for job in jobs:
        definition={'Label':job['name'],'ProgramArguments':[job['python'],*job['arguments']],
            'WorkingDirectory':job['cwd'],'ProcessType':'Background','RunAtLoad':job['interval'] is None}
        if job['interval']:definition['StartInterval']=job['interval']
        path=root/(job['name']+'.plist')
        if path.is_symlink():raise RuntimeError('LaunchAgent file is redirected')
        if path.exists():
            if plistlib.loads(path.read_bytes())!=definition:raise RuntimeError('Existing LaunchAgent binding differs; inspect before takeover')
        else:
            with path.open('xb') as out:plistlib.dump(definition,out)
        domain='gui/'+str(os.getuid())
        loaded=subprocess.run(['/bin/launchctl','print',domain+'/'+job['name']],capture_output=True,timeout=10)
        if loaded.returncode:
            subprocess.run(['/bin/launchctl','bootstrap',domain,str(path)],check=True,capture_output=True,timeout=20)

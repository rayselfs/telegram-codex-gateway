"""Windows process ownership and per-user secret storage."""
import ctypes
from ctypes import wintypes as w
import msvcrt
import os


class SingleInstance:
    def __init__(self, path):
        self.file = path.open('a+b')
        self.file.seek(0)
        if not os.fstat(self.file.fileno()).st_size:
            self.file.write(b'0')
            self.file.flush()
        self.file.seek(0)
        try:
            msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            self.file.close()
            raise RuntimeError('This gateway is already running')

    def close(self):
        self.file.close()


class BotInstance:
    def __init__(self,token,*,lock_root=None):
        import hashlib
        bot=token.partition(':')[0]
        if not bot.isascii() or not bot.isdigit() or ':' not in token:raise ValueError('Invalid bot identity')
        name='Global\\telegram-codex-bot-'+hashlib.sha256(bot.encode()).hexdigest()
        self.kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        self.kernel.CreateMutexW.argtypes=[ctypes.c_void_p,w.BOOL,w.LPCWSTR]
        self.kernel.CreateMutexW.restype=w.HANDLE
        self.kernel.CloseHandle.argtypes=[w.HANDLE]
        self.handle=self.kernel.CreateMutexW(None,False,name)
        error=ctypes.get_last_error()
        if not self.handle or error==183:
            self.close()
            raise RuntimeError('Bot is already running or its host lock is unavailable')

    def close(self):
        if self.handle:self.kernel.CloseHandle(self.handle);self.handle=None


def decrypt_token(path):
    class Blob(ctypes.Structure):
        _fields_ = [('size', w.DWORD), ('data', ctypes.POINTER(ctypes.c_byte))]
    data = path.read_bytes()
    buf = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte)))
    output = Blob()
    crypt = ctypes.windll.crypt32.CryptUnprotectData
    if not crypt(ctypes.byref(source), None, None, None, None, 0, ctypes.byref(output)):
        raise RuntimeError('Cannot decrypt Bot token for this Windows user')
    try:
        return ctypes.string_at(output.data, output.size).decode('utf-8')
    finally:
        ctypes.windll.kernel32.LocalFree(output.data)


class ChildJob:
    """Closing this handle terminates only this gateway's child process tree."""
    def __init__(self, process):
        class Basic(ctypes.Structure):
            _fields_ = [('per_process', ctypes.c_longlong), ('per_job', ctypes.c_longlong),
                        ('flags', w.DWORD), ('min_ws', ctypes.c_size_t), ('max_ws', ctypes.c_size_t),
                        ('active', w.DWORD), ('affinity', ctypes.c_size_t), ('priority', w.DWORD),
                        ('scheduling', w.DWORD)]
        class IO(ctypes.Structure):
            _fields_ = [(n, ctypes.c_ulonglong) for n in ('read_ops', 'write_ops', 'other_ops', 'read', 'write', 'other')]
        class Extended(ctypes.Structure):
            _fields_ = [('basic', Basic), ('io', IO), ('process_memory', ctypes.c_size_t),
                        ('job_memory', ctypes.c_size_t), ('peak_process', ctypes.c_size_t),
                        ('peak_job', ctypes.c_size_t)]
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel.CreateJobObjectW.restype = w.HANDLE
        self.kernel.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
        self.kernel.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        self.kernel.CloseHandle.argtypes = [w.HANDLE]
        self.handle = self.kernel.CreateJobObjectW(None, None)
        limits = Extended()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.handle or not self.kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            self.close()
            raise RuntimeError('Cannot configure child process cleanup')
        if not self.kernel.AssignProcessToJobObject(self.handle, w.HANDLE(int(process._handle))):
            self.close()
            raise RuntimeError('Cannot own Codex child process')

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None

def crypt(data,entropy,encrypt):
    class Blob(ctypes.Structure):
        _fields_=[('size',w.DWORD),('data',ctypes.POINTER(ctypes.c_byte))]
    def blob(value):
        buffer=ctypes.create_string_buffer(value)
        return buffer,Blob(len(value),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_byte)))
    source_buffer,source=blob(data);entropy_buffer,extra=blob(entropy);output=Blob()
    api=ctypes.windll.crypt32
    ok=(api.CryptProtectData(ctypes.byref(source),None,ctypes.byref(extra),None,None,1,ctypes.byref(output)) if encrypt else
        api.CryptUnprotectData(ctypes.byref(source),None,ctypes.byref(extra),None,None,1,ctypes.byref(output)))
    if not ok:raise RuntimeError('Credential encryption/decryption unavailable for this owner and Windows account')
    try:return ctypes.string_at(output.data,output.size)
    finally:
        ctypes.memset(output.data,0,output.size)
        ctypes.windll.kernel32.LocalFree(output.data)


def process_options():
    import subprocess
    return {"creationflags": subprocess.CREATE_NO_WINDOW}

def opened_path(source):
    from pathlib import Path
    fn = ctypes.windll.kernel32.GetFinalPathNameByHandleW
    fn.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32]
    buf = ctypes.create_unicode_buffer(32768)
    n = fn(msvcrt.get_osfhandle(source.fileno()), buf, len(buf), 0)
    if not n or n >= len(buf):
        raise ValueError("Cannot verify opened file location")
    actual = buf.value
    if actual.startswith("\\\\?\\"):
        actual = actual[4:]
    return Path(actual)


def install_background(jobs):
    import json,subprocess
    payload=[dict(job,argument_line=subprocess.list2cmdline(job['arguments'])) for job in jobs]
    script=r'''
$ErrorActionPreference='Stop'
$definitions=[Console]::In.ReadToEnd() | ConvertFrom-Json
$identity=[Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal=New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
foreach($job in $definitions){
 $existing=Get-ScheduledTask -TaskName $job.name -ErrorAction SilentlyContinue
 if($existing){
  if($existing.Actions.Count -ne 1 -or $existing.Actions[0].Execute -ne $job.python -or $existing.Actions[0].Arguments -ne $job.argument_line -or $existing.Actions[0].WorkingDirectory -ne $job.cwd){throw 'Existing task binding differs; inspect before takeover'}
  continue
 }
 $action=New-ScheduledTaskAction -Execute $job.python -Argument $job.argument_line -WorkingDirectory $job.cwd
 if($job.interval){
  $trigger=New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Seconds $job.interval)
  $limit=New-TimeSpan -Minutes 10
 }else{
  $trigger=New-ScheduledTaskTrigger -AtLogOn -User $identity
  $limit=[TimeSpan]::Zero
 }
 $settings=New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -ExecutionTimeLimit $limit -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
 Register-ScheduledTask -TaskName $job.name -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description 'Owner-bound Telegram Gateway background work' | Out-Null
}
'''
    subprocess.run([os.path.join(os.environ['SYSTEMROOT'],'System32/WindowsPowerShell/v1.0/powershell.exe'),
        '-NoProfile','-NonInteractive','-Command',script],input=json.dumps(payload),text=True,
        capture_output=True,check=True,timeout=60,**process_options())

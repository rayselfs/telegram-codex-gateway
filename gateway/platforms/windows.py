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

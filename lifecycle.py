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

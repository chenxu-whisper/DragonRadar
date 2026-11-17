"""Windows-only atomic DACL creation using standard-library Win32 bindings."""

import ctypes
import os
from contextlib import ExitStack
from ctypes import wintypes
from pathlib import Path


class SecurityAttributes(ctypes.Structure):
    _fields_ = [
        ("length", wintypes.DWORD),
        ("descriptor", wintypes.LPVOID),
        ("inherit", wintypes.BOOL),
    ]


class FileInformation(ctypes.Structure):
    _fields_ = [
        ("attributes", wintypes.DWORD),
        ("creation", wintypes.FILETIME),
        ("access", wintypes.FILETIME),
        ("write", wintypes.FILETIME),
        ("volume", wintypes.DWORD),
        ("size_high", wintypes.DWORD),
        ("size_low", wintypes.DWORD),
        ("links", wintypes.DWORD),
        ("index_high", wintypes.DWORD),
        ("index_low", wintypes.DWORD),
    ]


def open_private_file(path: Path, stack: ExitStack) -> int:
    """Pin non-reparse ancestors and CREATE_NEW with only current-user/SYSTEM ACL."""
    import msvcrt

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.GetCurrentProcess.argtypes = []
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel.LocalFree.restype = wintypes.HLOCAL
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(SecurityAttributes),
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.GetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(FileInformation),
    ]
    kernel.GetFileInformationByHandle.restype = wintypes.BOOL
    advapi.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi.OpenProcessToken.restype = wintypes.BOOL
    advapi.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi.GetTokenInformation.restype = wintypes.BOOL
    advapi.ConvertSidToStringSidW.argtypes = [
        wintypes.LPVOID,
        ctypes.POINTER(wintypes.LPWSTR),
    ]
    advapi.ConvertSidToStringSidW.restype = wintypes.BOOL
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL

    def checked(result):
        if not result:
            raise OSError("Cannot protect output file")
        return result

    def create(name, access, sharing, security, disposition, flags):
        handle = kernel.CreateFileW(
            "\\\\?\\" + str(name), access, sharing, security, disposition, flags, None
        )
        if handle == wintypes.HANDLE(-1).value:
            raise OSError("Cannot create protected output")
        return handle

    # Without FILE_SHARE_DELETE these ancestor handles prevent junction/rename
    # replacement between inspection and the final name-based CREATE_NEW.
    for ancestor in reversed(path.parents):
        handle = create(ancestor, 0, 3, None, 3, 0x02200000)
        stack.callback(kernel.CloseHandle, handle)
        info = FileInformation()
        checked(kernel.GetFileInformationByHandle(handle, ctypes.byref(info)))
        if info.attributes & 0x400 or not info.attributes & 0x10:
            raise OSError("Unsafe output ancestor")

    with ExitStack() as resources:
        token = wintypes.HANDLE()
        checked(
            advapi.OpenProcessToken(
                kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)
            )
        )
        resources.callback(kernel.CloseHandle, token)
        size = wintypes.DWORD()
        advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        checked(size.value)
        user = ctypes.create_string_buffer(size.value)
        checked(advapi.GetTokenInformation(token, 1, user, size, ctypes.byref(size)))
        sid = ctypes.cast(user, ctypes.POINTER(wintypes.LPVOID))[0]
        sid_text = wintypes.LPWSTR()
        checked(advapi.ConvertSidToStringSidW(sid, ctypes.byref(sid_text)))
        resources.callback(kernel.LocalFree, ctypes.cast(sid_text, wintypes.HLOCAL))
        descriptor = wintypes.LPVOID()
        checked(
            advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
                f"D:P(A;;FA;;;SY)(A;;FA;;;{sid_text.value})",
                1,
                ctypes.byref(descriptor),
                None,
            )
        )
        resources.callback(kernel.LocalFree, descriptor)
        security = SecurityAttributes(
            ctypes.sizeof(SecurityAttributes), descriptor, False
        )
        handle = create(path, 0x40000000, 0, ctypes.byref(security), 1, 0x00200080)
        try:
            info = FileInformation()
            checked(kernel.GetFileInformationByHandle(handle, ctypes.byref(info)))
            if info.attributes & 0x400 or info.links != 1:
                raise OSError("Unsafe output file")
            return msvcrt.open_osfhandle(handle, os.O_WRONLY | os.O_BINARY)
        except BaseException:
            kernel.CloseHandle(handle)
            path.unlink()
            raise

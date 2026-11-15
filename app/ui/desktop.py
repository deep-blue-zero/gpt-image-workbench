"""Local desktop actions for saved workbench files."""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path


def show_in_explorer(path: Path) -> None:
    """Open the parent folder and select the file, including paths with Unicode/spaces."""
    path = path.resolve(strict=True)
    if not path.is_file():
        raise ValueError("The selected output is not a file.")
    if sys.platform != "win32":
        raise OSError("Show in Explorer is available on Windows.")

    shell = ctypes.WinDLL("shell32")
    ole = ctypes.WinDLL("ole32")
    ole.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    ole.CoInitializeEx.restype = ctypes.c_long
    ole.CoUninitialize.argtypes = []
    ole.CoUninitialize.restype = None
    ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    ole.CoTaskMemFree.restype = None
    shell.SHParseDisplayName.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_ulong,
        ctypes.c_void_p,
    ]
    shell.SHParseDisplayName.restype = ctypes.c_long
    shell.SHOpenFolderAndSelectItems.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint,
        ctypes.c_void_p,
        ctypes.c_ulong,
    ]
    shell.SHOpenFolderAndSelectItems.restype = ctypes.c_long

    initialized = ole.CoInitializeEx(None, 2)
    # RPC_E_CHANGED_MODE means this worker thread already has a COM apartment.
    if initialized < 0 and initialized != -2147417850:
        raise OSError(f"Explorer initialization failed (0x{initialized & 0xFFFFFFFF:08X}).")
    item = ctypes.c_void_p()
    try:
        result = shell.SHParseDisplayName(str(path), None, ctypes.byref(item), 0, None)
        if result >= 0:
            result = shell.SHOpenFolderAndSelectItems(item, 0, None, 0)
        if result < 0:
            raise OSError(f"Windows could not reveal this file (0x{result & 0xFFFFFFFF:08X}).")
    finally:
        if item:
            ole.CoTaskMemFree(item)
        if initialized >= 0:
            ole.CoUninitialize()

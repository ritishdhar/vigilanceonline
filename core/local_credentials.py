"""Read Windows DPAPI credentials written by the local Telegram launcher."""

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import xml.etree.ElementTree as ET


def _decrypt(value: str) -> str:
    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    raw = bytes.fromhex(value)
    buffer = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
    source = Blob(len(raw), buffer)
    result = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt.CryptUnprotectData.argtypes = [
        ctypes.POINTER(Blob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(Blob),
    ]
    crypt.CryptUnprotectData.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if not crypt.CryptUnprotectData(
        ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)
    ):
        raise ValueError(
            "Saved credentials cannot be decrypted for this Windows account"
        )
    try:
        return ctypes.string_at(result.data, result.size).decode("utf-16-le")
    finally:
        ctypes.memset(result.data, 0, result.size)
        kernel.LocalFree(result.data)


def load_telegram_credentials(path: Path) -> dict[str, str]:
    """Load the backend bot secret in memory only."""
    if os.name != "nt" or not path.is_file():
        return {}
    try:
        root = ET.fromstring(path.read_bytes())
        values = {
            node.attrib.get("N"): node.text
            for node in root.iter("{http://schemas.microsoft.com/powershell/2004/04}SS")
        }
        bot = _decrypt(values["BotToken"])
        if not bot:
            raise ValueError
        return {"bot": bot}
    except Exception:
        raise ValueError(
            "Saved Telegram credentials could not be loaded. Run tools/start_telegram.ps1 -Reconfigure."
        ) from None

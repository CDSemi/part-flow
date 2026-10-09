"""Minimal inotify through ctypes (fault I8: observe workspace renames). Linux only; non-blocking reads."""
import ctypes
import ctypes.util
import os
import struct

IN_MOVED_FROM = 0x00000040
IN_MOVED_TO = 0x00000080
IN_CREATE = 0x00000100
IN_NONBLOCK = 0x00000800
IN_CLOEXEC = 0x00080000
EVENT_HEADER = struct.Struct("iIII")


class Watch:
    def __init__(self, path, mask=IN_MOVED_FROM | IN_MOVED_TO):
        name = ctypes.util.find_library("c") or "libc.so.6"
        self.libc = ctypes.CDLL(name, use_errno=True)
        self.fd = self.libc.inotify_init1(IN_NONBLOCK | IN_CLOEXEC)
        if self.fd < 0:
            raise OSError(ctypes.get_errno(), "inotify_init1 failed")
        self.wd = self.libc.inotify_add_watch(self.fd, str(path).encode(), mask)
        if self.wd < 0:
            error = ctypes.get_errno()
            os.close(self.fd)
            raise OSError(error, f"inotify_add_watch {path} failed")

    def read(self):
        """[(mask, name)] of the pending events (empty when none)."""
        try:
            data = os.read(self.fd, 65536)
        except BlockingIOError:
            return []
        events, offset = [], 0
        while offset + EVENT_HEADER.size <= len(data):
            _, mask, _, length = EVENT_HEADER.unpack_from(data, offset)
            raw = data[offset + EVENT_HEADER.size:offset + EVENT_HEADER.size + length]
            events.append((mask, raw.rstrip(b"\0").decode("utf-8", "replace")))
            offset += EVENT_HEADER.size + length
        return events

    def close(self):
        try:
            os.close(self.fd)
        except OSError:
            pass

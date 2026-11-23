"""Incremental JSON reading for large scanner reports. Stdlib only.

A Vuls report or a SARIF log can be tens of MB, and ``json.load`` of that on a
3.9 GB box costs many times the file size. ``Lazy`` walks the outer structure
itself and decodes only one member at a time with ``json.JSONDecoder.raw_decode``
(bounded by ``max_value``), so a 50 MB ``scannedCves`` object is processed one
CVE at a time.

Usage is cooperative: ``for key in lz.members():`` yields each key of the
current object and the caller MUST consume its value — ``lz.value()``,
``lz.skip()`` or a nested ``lz.members()`` / ``lz.items()`` — before the loop
continues.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

_DEC = json.JSONDecoder()
_WS = " \t\r\n"


class JsonStreamError(ValueError):
    pass


class Lazy:
    def __init__(self, fp, max_value: int = 32 * 1024 * 1024, chunk: int = 256 * 1024):
        self.fp = fp            # a text stream
        self.buf = ""
        self.pos = 0
        self.eof = False
        self.max_value = max_value
        self.chunk = chunk

    # ── buffer ───────────────────────────────────────────────────────────────
    def _fill(self, want: int | None = None) -> bool:
        if self.eof:
            return False
        data = self.fp.read(max(self.chunk, want or 0))
        if not data:
            self.eof = True
            return False
        self.buf = self.buf[self.pos:] + data
        self.pos = 0
        return True

    def peek(self) -> str:
        while True:
            while self.pos < len(self.buf) and self.buf[self.pos] in _WS:
                self.pos += 1
            if self.pos < len(self.buf):
                return self.buf[self.pos]
            if not self._fill():
                return ""

    def _expect(self, ch: str) -> None:
        got = self.peek()
        if got != ch:
            raise JsonStreamError(f"expected {ch!r}, found {got or 'end of file'!r}")
        self.pos += 1

    # ── values ───────────────────────────────────────────────────────────────
    def value(self):
        self.peek()
        while True:
            try:
                val, end = _DEC.raw_decode(self.buf, self.pos)
            except json.JSONDecodeError as exc:
                if self.eof:
                    raise JsonStreamError(f"invalid JSON: {exc.msg}") from exc
                if len(self.buf) - self.pos > self.max_value:
                    raise JsonStreamError(f"a single JSON value exceeds {self.max_value} bytes") from exc
                # Double the window each time so a big value costs log(n) retries.
                self._fill(len(self.buf) - self.pos)
                continue
            # A number (or literal) that ends exactly at the buffer edge may be
            # truncated ("12" + "34"): read on before trusting it.
            if end >= len(self.buf) and not self.eof and not isinstance(val, (dict, list, str)):
                if self._fill():
                    continue
            self.pos = end
            return val

    def skip(self) -> None:
        ch = self.peek()
        if ch == "{":
            for _ in self.members():
                self.skip()
        elif ch == "[":
            for _ in self.items():
                self.skip()
        else:
            self.value()

    def members(self) -> Iterator[str]:
        self._expect("{")
        if self.peek() == "}":
            self.pos += 1
            return
        while True:
            key = self.value()
            if not isinstance(key, str):
                raise JsonStreamError("object key is not a string")
            self._expect(":")
            yield key
            ch = self.peek()
            self.pos += 1
            if ch == "}":
                return
            if ch != ",":
                raise JsonStreamError(f"expected ',' or '}}', found {ch or 'end of file'!r}")

    def items(self) -> Iterator[int]:
        self._expect("[")
        if self.peek() == "]":
            self.pos += 1
            return
        n = 0
        while True:
            yield n
            n += 1
            ch = self.peek()
            self.pos += 1
            if ch == "]":
                return
            if ch != ",":
                raise JsonStreamError(f"expected ',' or ']', found {ch or 'end of file'!r}")

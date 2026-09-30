"""The PII gate: a hard filter between the state builder and the network (PLAN 3.5).

`PIIGate.scan(payload)` walks dicts (keys **and** values), lists, tuples, strings and numbers and returns one
`PIIFinding(detector, path, masked)` per hit. `check` raises `PIIBlocked` on any finding. Findings carry masked
values only, so they are safe to write to the audit log; a path that would itself contain PII (a PII-shaped
dict key) is replaced by `<key>`.

Beyond the detectors the gate adds three structural defences:
  * key context: a value under a key such as `applicant_name` or `pincode` is scanned as if it carried its
    label ("Applicant name: <value>"), so a bare unknown name or PIN in a labelled field is caught;
  * strings that hold JSON are parsed and walked, so paths stay precise;
  * long base64 / hex tokens are decoded and scanned once, to defeat trivial encoding.

Speed: scanning results are cached per distinct string (state vocabulary, keys and boilerplate repeat across
requests), and cheap prefilters skip detector groups that cannot match.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from jevloan.canonical import to_jsonable
from jevloan.pii.detectors import STATE_VOCAB, is_allowed_token, run_all_masked

__all__ = ["PIIFinding", "PIIBlocked", "PIIGate"]

_MAX_FINDINGS = 200
_MAX_DEPTH = 64


@dataclass(frozen=True, slots=True)
class PIIFinding:
    detector: str
    path: str
    masked: str

    def as_dict(self) -> dict[str, str]:
        return {"detector": self.detector, "path": self.path, "masked": self.masked}


class PIIBlocked(Exception):
    """Raised by `PIIGate.check`. `.findings` lists what was found (masked values only)."""

    def __init__(self, findings: Sequence[PIIFinding] | str = (), message: str | None = None) -> None:
        if isinstance(findings, str):  # constructed from a message alone (copies, re-raises)
            message, findings = findings, ()
        self.findings: list[PIIFinding] = list(findings)
        if message is None:
            shown = "; ".join(f"{f.detector} at {f.path} ({f.masked})" for f in self.findings[:5])
            more = f" (+{len(self.findings) - 5} more)" if len(self.findings) > 5 else ""
            message = f"PII gate blocked the payload: {len(self.findings)} finding(s): {shown}{more}"
        super().__init__(message)

    @property
    def detectors(self) -> set[str]:
        return {f.detector for f in self.findings}


# --------------------------------------------------------------------------------------------------
# Cached string scanning
# --------------------------------------------------------------------------------------------------

_LONG_TOKEN_RE = re.compile(r"[A-Za-z0-9+/_\-]{16,}={0,2}")
_HEX_TOKEN_RE = re.compile(r"(?:[0-9a-fA-F]{2}){8,}")


def _printable_text(raw: bytes) -> str | None:
    try:
        txt = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if len(txt) < 6 or not txt.isprintable():
        return None
    if sum(ch.isalnum() for ch in txt) < 6:
        return None
    return txt


def _decoded_layers(s: str) -> list[str]:
    out: list[str] = []
    for m in _LONG_TOKEN_RE.finditer(s):
        tok = m.group(0)
        if _HEX_TOKEN_RE.fullmatch(tok):
            try:
                txt = _printable_text(bytes.fromhex(tok))
            except ValueError:
                txt = None
            if txt:
                out.append(txt)
        try:
            pad = "=" * (-len(tok.rstrip("=")) % 4)
            body = tok.rstrip("=") + pad
            raw = base64.b64decode(body.replace("-", "+").replace("_", "/"), validate=True)
        except (binascii.Error, ValueError):
            continue
        txt = _printable_text(raw)
        if txt:
            out.append(txt)
    return out


@lru_cache(maxsize=16384)
def _scan_str(s: str) -> tuple[tuple[str, str], ...]:
    """(detector, masked) pairs for one string, including one decoding layer of base64/hex tokens."""
    found = {(f.detector, f.masked) for f in run_all_masked(s)}
    if len(s) >= 16:
        for layer in _decoded_layers(s):
            found.update((f.detector, f.masked) for f in run_all_masked(layer))
    return tuple(sorted(found))


@lru_cache(maxsize=4096)
def _scan_key(key: str) -> tuple[tuple[str, str], ...]:
    found = set(_scan_str(key))
    if "_" in key or "-" in key:
        found.update(_scan_str(key.replace("_", " ")))
    return tuple(sorted(found))


_NAME_KEY_RE = re.compile(
    r"(?:(?:full|first|last|middle|given|family|sur|applicant|co applicant|borrower|guarantor|proprietor|"
    r"account holder|holder|customer|consumer|employee|father|mother|spouse|husband|wife|nominee|signatory|"
    r"beneficiary|payee|director|partner|owner|tenant|landlord|person|individual|legal|registered)[ ])?"
    r"(?:name|names)|(?:applicant|co applicant|coapplicant|borrower|co borrower|guarantor|proprietor|"
    r"account holder|holder|customer|consumer|employee|father|mother|spouse|husband|wife|nominee|signatory|"
    r"beneficiary|payee|director|partner|owner|tenant|landlord|surname)"
)
_PIN_KEY_RE = re.compile(r"(?:pin|pincode|pin code|postal code|post code|zip|zip code|pin no|pincode no)")


@lru_cache(maxsize=4096)
def _key_context(key: str) -> str | None:
    norm = re.sub(r"[\s_\-]+", " ", key.lower()).strip()
    if _NAME_KEY_RE.fullmatch(norm):
        return "name"
    if _PIN_KEY_RE.fullmatch(norm):
        return "pin"
    return None


@lru_cache(maxsize=8192)
def _scan_ctx(kind: str, value: str) -> tuple[tuple[str, str], ...]:
    """Scan a bare value as if it carried its label. Only findings the label itself makes possible count."""
    if kind == "name":
        text = f"Applicant name: {value}"
        keep = {"person_name"}
    else:
        text = f"PIN: {value}"
        keep = {"pincode_ctx"}
    return tuple(sorted((f.detector, f.masked) for f in run_all_masked(text) if f.detector in keep))


_SIMPLE_KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_\-]*")


@lru_cache(maxsize=4096)
def _key_segment(key: str) -> str:
    if _SIMPLE_KEY_RE.fullmatch(key):
        return "." + key
    return "[" + json.dumps(key, ensure_ascii=False) + "]"


def _join(segs: list[str]) -> str:
    if not segs:
        return "$"
    path = "".join(segs)
    return path[1:] if path.startswith(".") else path


_IDENT_RE = re.compile(r"[A-Za-z0-9_.:\-]+")


def _skip_ctx(s: str) -> bool:
    """Values that cannot be a bare personal name or PIN: redaction tokens, enum vocabulary, snake_case ids."""
    return is_allowed_token(s) or s in STATE_VOCAB or ("_" in s and _IDENT_RE.fullmatch(s) is not None)


class PIIGate:
    """Scan JSON-like payloads for personal identifiers. Stateless apart from module-level string caches."""

    def scan(self, payload: Any) -> list[PIIFinding]:
        out: list[PIIFinding] = []
        self._walk(payload, [], out, None, 0)
        return out

    def check(self, payload: Any) -> None:
        findings = self.scan(payload)
        if findings:
            raise PIIBlocked(findings)

    # ---------------------------------------------------------------------------------------------

    def _emit(self, pairs: tuple[tuple[str, str], ...], segs: list[str], out: list[PIIFinding]) -> None:
        if not pairs or len(out) >= _MAX_FINDINGS:
            return
        path = _join(segs)
        for detector, masked in pairs:
            out.append(PIIFinding(detector, path, masked))

    def _walk(self, node: Any, segs: list[str], out: list[PIIFinding], ctx: str | None, depth: int) -> None:
        if len(out) >= _MAX_FINDINGS:
            return
        if depth > _MAX_DEPTH:
            out.append(PIIFinding("depth_limit", _join(segs), "*"))  # refuse absurd nesting rather than skip it
            return
        t = type(node)
        if t is str:
            self._string(node, segs, out, ctx, depth)
        elif t is dict or isinstance(node, Mapping):
            for k, v in node.items():
                ks = k if type(k) is str else str(k)
                key_hits = _scan_key(ks)
                if key_hits:
                    segs.append(".<key>")  # never put a PII-shaped key into a path
                    self._emit(key_hits, segs, out)
                else:
                    segs.append(_key_segment(ks))
                self._walk(v, segs, out, _key_context(ks), depth + 1)
                segs.pop()
        elif t is list or t is tuple or isinstance(node, (list, tuple, set, frozenset)):
            for i, v in enumerate(node):
                segs.append(f"[{i}]")
                self._walk(v, segs, out, ctx, depth + 1)
                segs.pop()
        elif t is bool or node is None:
            return
        elif t is int:
            if ctx == "pin" or abs(node) >= 100_000:
                self._string(str(node), segs, out, ctx, depth)
        elif t is float:
            if node == node and abs(node) >= 1e5 and abs(node) < 1e30:
                self._string(str(int(abs(node))), segs, out, ctx, depth)
        elif t is bytes or t is bytearray:
            self._string(bytes(node).decode("utf-8", errors="replace"), segs, out, ctx, depth)
        else:
            try:
                converted = to_jsonable(node)
            except TypeError:  # unknown type: scan its string form rather than let it through
                self._string(str(node), segs, out, ctx, depth)
            else:
                self._walk(converted, segs, out, ctx, depth + 1)

    def _string(self, s: str, segs: list[str], out: list[PIIFinding], ctx: str | None, depth: int) -> None:
        if len(s) < 2:
            return
        if s[0] in "{[" and s[-1] in "}]" and len(s) > 2:
            try:
                parsed = json.loads(s)
            except ValueError:
                parsed = None
            if parsed is not None and not isinstance(parsed, (str, int, float, bool)):
                self._walk(parsed, segs, out, None, depth + 1)
                return
        self._emit(_scan_str(s), segs, out)
        if ctx is not None and not _skip_ctx(s):
            self._emit(_scan_ctx(ctx, s), segs, out)

"""Network-boundary PII guard (PLAN 3.5): a second, independent check behind the gateway's own.

`PIIEgressGuardTransport` wraps any `httpx2.AsyncBaseTransport`. It reads the request body, decodes it as JSON
(or scans it as raw text when it is not JSON), scans the URL path and query string too, and raises
`PIIEgressBlocked` **before** the inner transport is called if anything is found. Nothing is sent on a block.
The guard fails closed: a body it cannot inspect (an unknown `Content-Encoding`) or a gate that raises blocks
the request as well.
"""

from __future__ import annotations

import gzip
import json
import zlib
from collections.abc import Sequence
from typing import Any
from urllib.parse import parse_qsl, unquote

import httpx2

from jevloan.pii.gate import PIIFinding, PIIGate

__all__ = ["PIIEgressBlocked", "PIIEgressGuardTransport"]


class PIIEgressBlocked(Exception):
    """A request was stopped at the network boundary. Deliberately a plain Exception (not an httpx2 error) that
    can be rebuilt from one string, because the SDK re-creates wrapped exceptions with `type(exc)(str(exc))`.
    `.findings` holds masked findings and is empty on such a copy."""

    def __init__(self, message: str = "PII egress blocked", findings: Sequence[PIIFinding] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.findings: list[PIIFinding] = list(findings or [])


def _decode_body(body: bytes, encoding: str) -> tuple[bytes | None, str | None]:
    """Undo Content-Encoding where we can. Returns (bytes, None) or (None, reason it cannot be inspected)."""
    encoding = encoding.strip().lower()
    if encoding in ("", "identity"):
        return body, None
    try:
        if encoding in ("gzip", "x-gzip"):
            return gzip.decompress(body), None
        if encoding == "deflate":
            try:
                return zlib.decompress(body), None
            except zlib.error:
                return zlib.decompress(body, -zlib.MAX_WBITS), None
    except (OSError, EOFError, zlib.error):
        return None, f"corrupt {encoding} body"
    return None, f"unsupported content-encoding {encoding!r}"


class PIIEgressGuardTransport(httpx2.AsyncBaseTransport):
    def __init__(self, inner: httpx2.AsyncBaseTransport, gate: PIIGate) -> None:
        self.inner = inner
        self.gate = gate

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        body = await request.aread()
        try:
            findings = self._scan_request(request, body)
        except Exception as exc:  # fail closed
            raise PIIEgressBlocked(f"PII egress guard could not inspect the request ({type(exc).__name__})") from None
        if findings:
            shown = ", ".join(sorted({f.detector for f in findings}))
            raise PIIEgressBlocked(
                f"PII egress guard blocked the request: {len(findings)} finding(s) ({shown})", findings=findings
            )
        return await self.inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self.inner.aclose()

    # ---------------------------------------------------------------------------------------------

    def _scan_request(self, request: httpx2.Request, body: bytes) -> list[PIIFinding]:
        url = request.url
        query_raw = url.query.decode("utf-8", errors="replace") if isinstance(url.query, bytes) else str(url.query)
        payload: dict[str, Any] = {
            "url": {
                "path": unquote(url.path),
                "query": unquote(query_raw.replace("+", " ")),
                "params": {k: v for k, v in parse_qsl(query_raw, keep_blank_values=True)},
            }
        }
        findings = list(self.gate.scan(payload))
        if body:
            data, problem = _decode_body(body, request.headers.get("content-encoding", ""))
            if data is None:
                findings.append(PIIFinding("unscannable_body", "body", problem or "*"))
            else:
                text = data.decode("utf-8", errors="replace")
                try:
                    parsed: Any = json.loads(text)
                except ValueError:
                    parsed = text
                findings += self.gate.scan({"body": parsed})
        return findings

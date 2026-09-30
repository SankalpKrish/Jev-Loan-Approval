import json
import time

import httpx2
import pytest

from jevloan.jev.chaos import (
    ConnectionDropTransport,
    CountingTransport,
    SlowTransport,
    StatusTransport,
    SwitchableTransport,
)

URL = "https://chaos.test/v1/systemone"


def echo(request: httpx2.Request) -> httpx2.Response:
    return httpx2.Response(200, json={"echo": json.loads(request.content)})


async def post(transport: httpx2.AsyncBaseTransport, body: object = None) -> httpx2.Response:
    async with httpx2.AsyncClient(transport=transport) as client:
        return await client.post(URL, json=body if body is not None else {"state": "s"})


async def test_slow_transport_delays_then_forwards():
    start = time.monotonic()
    response = await post(SlowTransport(httpx2.MockTransport(echo), delay_s=0.2))
    assert time.monotonic() - start >= 0.19
    assert response.json() == {"echo": {"state": "s"}}


@pytest.mark.parametrize("status", [401, 422, 429, 500, 529])
async def test_status_transport_returns_that_status_with_a_json_error_body(status):
    response = await post(StatusTransport(status))
    assert response.status_code == status
    assert str(status) in response.json()["error"]


async def test_connection_drop_raises_connect_error():
    with pytest.raises(httpx2.ConnectError):
        await post(ConnectionDropTransport())


async def test_switchable_transport_can_be_killed_and_restored():
    switch = SwitchableTransport(httpx2.MockTransport(echo))
    assert not switch.killed
    assert (await post(switch)).status_code == 200
    switch.kill()
    assert switch.killed
    with pytest.raises(httpx2.ConnectError):
        await post(switch)
    switch.restore()
    assert not switch.killed
    assert (await post(switch)).status_code == 200


async def test_counting_transport_keeps_decoded_bodies_and_forwards():
    counting = CountingTransport(httpx2.MockTransport(echo))
    await post(counting, {"state": "one"})
    await post(counting, {"state": "two"})
    assert counting.requests == [{"state": "one"}, {"state": "two"}]


async def test_counting_transport_sees_requests_that_the_inner_transport_rejects():
    counting = CountingTransport(StatusTransport(500))
    assert (await post(counting)).status_code == 500
    assert len(counting.requests) == 1


async def test_wrappers_close_their_inner_transport():
    closed = []

    class Inner(httpx2.AsyncBaseTransport):
        async def aclose(self) -> None:
            closed.append(True)

    await SwitchableTransport(CountingTransport(Inner())).aclose()
    assert closed == [True]

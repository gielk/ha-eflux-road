"""Focused tests for login and error handling, using a local fake Road API."""

import asyncio
import importlib
import sys
import types
from pathlib import Path
from unittest.mock import patch

from aiohttp import ClientSession, web

package = types.ModuleType("eflux_road")
package.__path__ = [str(Path(__file__).resolve().parents[1] / "custom_components" / "eflux_road")]
sys.modules["eflux_road"] = package
api = importlib.import_module("eflux_road.api")


async def main():
    seen = []
    slow_started, finish_slow = asyncio.Event(), asyncio.Event()

    async def route(request):
        seen.append((request.method, request.path, request.headers.get("Authorization")))
        if request.path == "/1/auth/login":
            payload = await request.json()
            if payload["email"] == "verification":
                return web.Response(status=403, text="Verification required", content_type="text/html")
            if payload["email"] == "blocked":
                return web.json_response({"cloudflare_error": True, "error_code": 1010}, status=403)
            if payload["email"] == "invalid":
                return web.json_response({"error": "invalid"}, status=403)
            if payload["email"] == "mfa":
                return web.json_response({"data": {"mfaRequired": True}})
            if payload["email"] == "mfa-supported":
                return web.json_response({"data": {"mfaRequired": True, "mfa": {
                    "pendingId": "synthetic-pending", "methods": ["passkey", "totp", "sms"]}}})
            return web.json_response({"data": {"token": "login-token"}})
        if request.path == "/1/auth/whoami":
            return web.json_response({"data": {"type": "user"}})
        if request.path == "/1/auth/refresh":
            return web.json_response({"data": {"token": "rotated-token"}})
        if request.path == "/1/auth/mfa/challenge":
            assert await request.json() == {"pendingId": "synthetic-pending", "method": "sms"}
            return web.json_response({"data": {}})
        if request.path in ("/1/auth/mfa/totp/verify", "/1/auth/mfa/sms/verify"):
            payload = await request.json()
            assert payload["pendingId"] == "synthetic-pending"
            if payload["code"] != "012345":
                return web.json_response({"error": "bad code"}, status=400)
            return web.json_response({"data": {"token": "mfa-token"}})
        if request.path == "/hint":
            return web.json_response({"data": {}}, headers={"X-Token-Status": "refresh"})
        if request.path == "/slow-old-hint":
            slow_started.set()
            await finish_slow.wait()
            return web.json_response({"data": {}}, headers={"X-Token-Status": "refresh"})
        if request.path == "/1/remote-sessions/msp":
            return web.json_response({"error": {"message": "private"}}, status=500)
        raise web.HTTPNotFound()

    app = web.Application()
    app.router.add_route("*", "/{path:.*}", route)
    runner = web.AppRunner(app)
    await runner.setup()
    server = web.TCPSite(runner, "127.0.0.1", 0)
    await server.start()
    port = server._server.sockets[0].getsockname()[1]
    try:
        with patch.object(api, "API_URL", f"http://127.0.0.1:{port}"):
            async with ClientSession() as session:
                client = api.EFluxClient(session)
                assert await client.login("test", "password") == "login-token"
                await client.validate()
                await client.request("GET", "/hint", auth=False)
                assert not client.refresh_requested
                await client.request("GET", "/hint")
                assert client.refresh_requested
                assert await client.refresh() == "rotated-token"
                assert not client.refresh_requested
                client.token = "old-token"
                old_request = asyncio.create_task(client.request("GET", "/slow-old-hint"))
                await slow_started.wait()
                await client.refresh()
                finish_slow.set()
                await old_request
                assert client.token == "rotated-token"
                assert not client.refresh_requested, "A late response for the old token must be ignored"
                try:
                    await client.request("POST", "/1/remote-sessions/msp", payload={})
                except api.EFluxRequestError as err:
                    assert err.status == 500
                    assert "private" not in str(err)
                else:
                    raise AssertionError("Expected 500")
                for account in ("verification", "blocked", "mfa"):
                    try:
                        await client.login(account, "password")
                    except api.EFluxVerificationRequired:
                        pass
                    else:
                        raise AssertionError(f"{account} must require verification")
                try:
                    await client.login("invalid", "password")
                except api.EFluxVerificationRequired:
                    raise AssertionError("A normal 403 must not be reported as a browser block")
                except api.EFluxAuthError:
                    pass
                else:
                    raise AssertionError("Expected an authentication error")
                try:
                    await client.login("mfa-supported", "password")
                except api.EFluxMfaRequired as challenge:
                    assert challenge.pending_id == "synthetic-pending"
                    assert challenge.methods == ("totp", "sms")
                    assert "synthetic-pending" not in str(challenge)
                    mfa_pending = challenge.pending_id
                else:
                    raise AssertionError("Expected an MFA challenge")
                await client.send_sms_code(mfa_pending)
                for method in ("totp", "sms"):
                    try:
                        await client.verify_mfa("synthetic-pending", method, "000000")
                    except api.EFluxRequestError as err:
                        assert err.status == 400
                    else:
                        raise AssertionError("Expected an incorrect code to fail")
                    assert await client.verify_mfa("synthetic-pending", method, "012345") == "mfa-token"
        assert sum(path == "/1/remote-sessions/msp" for _, path, _ in seen) == 1
        print("Login, verificatie, sessieverversing en eenmalig 500-verzoek gecontroleerd.")
    finally:
        await runner.cleanup()


asyncio.run(main())

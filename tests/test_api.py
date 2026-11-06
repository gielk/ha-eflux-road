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

    async def route(request):
        seen.append((request.method, request.path, request.headers.get("Authorization")))
        if request.path == "/1/auth/login":
            payload = await request.json()
            if payload["email"] == "verification":
                return web.Response(status=403, text="Verification required", content_type="text/html")
            if payload["email"] == "mfa":
                return web.json_response({"data": {"mfaRequired": True}})
            return web.json_response({"data": {"token": "login-token"}})
        if request.path == "/1/auth/whoami":
            return web.json_response({"data": {"type": "user"}})
        if request.path == "/1/auth/refresh":
            return web.json_response({"data": {"token": "rotated-token"}})
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
                assert await client.refresh() == "rotated-token"
                try:
                    await client.request("POST", "/1/remote-sessions/msp", payload={})
                except api.EFluxRequestError as err:
                    assert err.status == 500
                    assert "private" not in str(err)
                else:
                    raise AssertionError("Expected 500")
                for account in ("verification", "mfa"):
                    try:
                        await client.login(account, "password")
                    except api.EFluxVerificationRequired:
                        pass
                    else:
                        raise AssertionError(f"{account} must require verification")
        assert sum(path == "/1/remote-sessions/msp" for _, path, _ in seen) == 1
        print("Login, verificatie, sessieverversing en eenmalig 500-verzoek gecontroleerd.")
    finally:
        await runner.cleanup()


asyncio.run(main())

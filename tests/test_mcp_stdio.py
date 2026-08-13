"""Agent-level smoke of the ``crcglot-mcp`` stdio transport.

The rest of ``test_mcp.py`` drives the server in-process, which never
exercises what a real MCP client (an agent) actually does: spawn the
``crcglot-mcp`` process, negotiate a protocol version over JSON-RPC on
stdio, and speak the wire.  This module holds that layer, deliberately
negotiating an OLDER protocol revision than the SDK's latest, so a
client that has not upgraded keeps working -- the mcp 2.0 port was
verified with exactly this conversation before it shipped.

One conversation, one subprocess: initialize -> initialized ->
tools/list -> tools/call -> resources/read -> prompts/get, asserted in
a single test so the process is spawned once (per the batch-tier
spirit; a per-request test would spawn the server per case).
"""

from __future__ import annotations

import json
import queue
import shutil
import subprocess
import threading

import pytest

from crcglot import LANGUAGES
from crcglot.verbs import VERBS

HAS_UV = shutil.which("uv") is not None

# The protocol revision an older agent would request -- deliberately not
# the SDK's latest, so version negotiation itself is under test.
_OLD_PROTOCOL = "2025-03-26"


@pytest.mark.slow
@pytest.mark.skipif(not HAS_UV, reason="uv not on PATH (spawns crcglot-mcp)")
class TestStdioAgentConversation:
    """A full agent conversation against the real spawned server."""

    def test_old_protocol_agent_full_conversation(self, tmp_path):
        # Arrange -- spawn the real entry point and a stdout reader.
        proc = subprocess.Popen(
            ["uv", "run", "crcglot-mcp"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8",
        )
        responses: queue.Queue[dict] = queue.Queue()

        def reader() -> None:
            assert proc.stdout is not None
            for line in proc.stdout:
                line = line.strip()
                if line.startswith("{"):
                    msg = json.loads(line)
                    if "id" in msg:
                        responses.put(msg)

        threading.Thread(target=reader, daemon=True).start()

        def send(msg: dict) -> None:
            assert proc.stdin is not None
            proc.stdin.write(json.dumps(msg) + "\n")
            proc.stdin.flush()

        def rpc(msg: dict) -> dict:
            send(msg)
            reply = responses.get(timeout=60)
            assert "error" not in reply, (
                f"request id={msg.get('id')} failed: {reply.get('error')}"
            )
            return reply["result"]

        try:
            # Act -- the conversation an agent has on connect.
            init = rpc({
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {
                    "protocolVersion": _OLD_PROTOCOL,
                    "capabilities": {},
                    "clientInfo": {"name": "crcglot-stdio-test", "version": "0"},
                },
            })
            send({"jsonrpc": "2.0", "method": "notifications/initialized"})
            tools = rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["tools"]
            call = rpc({
                "jsonrpc": "2.0", "id": 3, "method": "tools/call",
                "params": {
                    "name": "crc_compute",
                    "arguments": {"algorithm": "crc32", "data_text": "123456789"},
                },
            })
            resource = rpc({
                "jsonrpc": "2.0", "id": 4, "method": "resources/read",
                "params": {"uri": "crcglot://languages.json"},
            })
            prompt = rpc({
                "jsonrpc": "2.0", "id": 5, "method": "prompts/get",
                "params": {
                    "name": "design-a-crc",
                    "arguments": {"use_case": "a new serial link"},
                },
            })
        finally:
            if proc.stdin is not None:
                proc.stdin.close()
            proc.wait(timeout=30)

        # Assert -- negotiation: the server accepted the old revision
        # rather than forcing its latest on the client.
        actual_protocol = init["protocolVersion"]
        assert actual_protocol == _OLD_PROTOCOL, (
            f"server forced protocol {actual_protocol!r}; an old agent "
            f"asking for {_OLD_PROTOCOL!r} must be accepted"
        )
        assert init.get("instructions"), "instructions missing from initialize"

        # Assert -- the wire is camelCase JSON with the full tool set.
        assert len(tools) == len(VERBS), (
            f"expected {len(VERBS)} tools on the wire, got {len(tools)}"
        )
        assert all("inputSchema" in t for t in tools), (
            "wire tools must carry camelCase inputSchema"
        )

        # Assert -- a real computation round-tripped the pipe.
        crc_hex = call["structuredContent"]["crc_hex"]
        assert crc_hex.lower() == "0xcbf43926", (
            f"crc_compute over stdio returned {crc_hex}, expected 0xCBF43926"
        )

        # Assert -- resources serve the live registry (all targets).
        langs = json.loads(resource["contents"][0]["text"])["languages"]
        assert set(langs) == set(LANGUAGES), (
            f"languages resource drifted from the registry: "
            f"{sorted(set(langs) ^ set(LANGUAGES))}"
        )

        # Assert -- prompt rendering interpolates over the wire.
        text = prompt["messages"][0]["content"]["text"]
        assert "serial link" in text, "use_case not interpolated via stdio"

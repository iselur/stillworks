"""Tests for stillworks MCP server — drive _handle() directly."""

import json
import os
import sys
import tempfile
import unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# Make sure subprocesses spawned by _run_cli can also import stillworks.
_pp = os.environ.get("PYTHONPATH", "")
if _REPO_ROOT not in _pp.split(":"):
    os.environ["PYTHONPATH"] = (_REPO_ROOT + ":" + _pp) if _pp else _REPO_ROOT

from stillworks import mcp_server


def _req(method, params=None, req_id=1):
    msg = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        msg["params"] = params
    return msg


def _notif(method, params=None):
    """A notification has no 'id'."""
    msg = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        msg["params"] = params
    return msg


class TestMCPHandle(unittest.TestCase):

    # ------------------------------------------------------------------
    # initialize
    # ------------------------------------------------------------------

    def test_initialize_returns_protocol_version(self):
        resp = mcp_server._handle(_req("initialize"))
        self.assertIn("result", resp)
        self.assertEqual(resp["result"]["protocolVersion"], "2024-11-05")
        self.assertIn("serverInfo", resp["result"])

    def test_initialize_echoes_request_id(self):
        resp = mcp_server._handle(_req("initialize", req_id=42))
        self.assertEqual(resp["id"], 42)

    # ------------------------------------------------------------------
    # tools/list
    # ------------------------------------------------------------------

    def test_tools_list_returns_all_tools(self):
        resp = mcp_server._handle(_req("tools/list"))
        self.assertIn("result", resp)
        names = {t["name"] for t in resp["result"]["tools"]}
        self.assertIn("stillworks_lock", names)
        self.assertIn("stillworks_check", names)
        self.assertIn("stillworks_accept", names)
        self.assertIn("stillworks_report", names)

    def test_tools_list_tools_have_input_schema(self):
        resp = mcp_server._handle(_req("tools/list"))
        for tool in resp["result"]["tools"]:
            self.assertIn("inputSchema", tool)

    # ------------------------------------------------------------------
    # unknown method → -32601
    # ------------------------------------------------------------------

    def test_unknown_method_returns_32601(self):
        resp = mcp_server._handle(_req("no/such/method"))
        self.assertIn("error", resp)
        self.assertEqual(resp["error"]["code"], -32601)

    def test_unknown_method_has_no_result_key(self):
        resp = mcp_server._handle(_req("no/such/method"))
        self.assertNotIn("result", resp)

    # ------------------------------------------------------------------
    # notifications → None (no response)
    # ------------------------------------------------------------------

    def test_initialized_notification_returns_none(self):
        resp = mcp_server._handle(_notif("notifications/initialized"))
        self.assertIsNone(resp)

    def test_unknown_notification_returns_none(self):
        # Any message without 'id' is a notification; must not get a response.
        resp = mcp_server._handle(_notif("some/unknown/notification"))
        self.assertIsNone(resp)

    # ------------------------------------------------------------------
    # ping
    # ------------------------------------------------------------------

    def test_ping_returns_empty_result(self):
        resp = mcp_server._handle(_req("ping"))
        self.assertIn("result", resp)
        self.assertEqual(resp["result"], {})

    # ------------------------------------------------------------------
    # tools/call — missing project_dir
    # ------------------------------------------------------------------

    def test_tools_call_missing_project_dir_is_error(self):
        resp = mcp_server._handle(_req("tools/call", {
            "name": "stillworks_lock",
            "arguments": {},  # project_dir absent
        }))
        self.assertIn("result", resp)
        self.assertTrue(resp["result"]["isError"])

    # ------------------------------------------------------------------
    # tools/call — lock + check on a real temp project
    # ------------------------------------------------------------------

    def test_tools_call_lock_then_check(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "mymod.py")
            with open(path, "w") as f:
                f.write("def add(x: int, y: int) -> int:\n    return x + y\n")

            # Lock.
            lock_resp = mcp_server._handle(_req("tools/call", {
                "name": "stillworks_lock",
                "arguments": {
                    "project_dir": tmpdir,
                    "target": path,
                    "fuzz": 3,
                },
            }, req_id=10))
            self.assertIn("result", lock_resp)
            self.assertFalse(lock_resp["result"]["isError"],
                             msg=lock_resp["result"]["content"])

            # Check.
            check_resp = mcp_server._handle(_req("tools/call", {
                "name": "stillworks_check",
                "arguments": {"project_dir": tmpdir},
            }, req_id=11))
            self.assertIn("result", check_resp)
            # isError for check means exit > 1 (usage error), not a behavior change.
            self.assertFalse(check_resp["result"]["isError"],
                             msg=check_resp["result"]["content"])
            # Response text should contain valid JSON from --json flag.
            text = check_resp["result"]["content"][0]["text"]
            parsed = json.loads(text)
            self.assertTrue(parsed.get("ok"), msg=text)

    def test_tools_call_accept_all(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "mymod.py")
            with open(path, "w") as f:
                f.write("def add(x: int, y: int) -> int:\n    return x + y\n")

            # Lock, change code, then accept --all.
            mcp_server._handle(_req("tools/call", {
                "name": "stillworks_lock",
                "arguments": {"project_dir": tmpdir, "target": path, "fuzz": 3},
            }))
            with open(path, "w") as f:
                f.write("def add(x: int, y: int) -> int:\n    return x + y + 100\n")

            accept_resp = mcp_server._handle(_req("tools/call", {
                "name": "stillworks_accept",
                "arguments": {"project_dir": tmpdir, "accept_all": True},
            }, req_id=20))
            self.assertIn("result", accept_resp)
            self.assertFalse(accept_resp["result"]["isError"],
                             msg=accept_resp["result"]["content"])

    def test_tools_call_report(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "mymod.py")
            with open(path, "w") as f:
                f.write("def add(x: int, y: int) -> int:\n    return x + y\n")
            mcp_server._handle(_req("tools/call", {
                "name": "stillworks_lock",
                "arguments": {"project_dir": tmpdir, "target": path, "fuzz": 3},
            }))
            report_resp = mcp_server._handle(_req("tools/call", {
                "name": "stillworks_report",
                "arguments": {"project_dir": tmpdir},
            }, req_id=30))
            self.assertIn("result", report_resp)
            self.assertFalse(report_resp["result"]["isError"])
            text = report_resp["result"]["content"][0]["text"]
            self.assertIn("stillworks evidence report", text)

    def test_tools_call_unknown_tool_is_error(self):
        resp = mcp_server._handle(_req("tools/call", {
            "name": "nonexistent_tool",
            "arguments": {"project_dir": "/tmp"},
        }))
        self.assertIn("result", resp)
        self.assertTrue(resp["result"]["isError"])

    # ------------------------------------------------------------------
    # serve() — newline-delimited JSON-RPC loop
    # ------------------------------------------------------------------

    def test_serve_handles_one_message(self):
        import io
        msg = json.dumps(_req("tools/list")) + "\n"
        stdin = io.StringIO(msg)
        stdout = io.StringIO()
        mcp_server.serve(stdin=stdin, stdout=stdout)
        stdout.seek(0)
        resp = json.loads(stdout.read().strip())
        self.assertIn("result", resp)

    def test_serve_skips_blank_lines(self):
        import io
        msg = "\n\n" + json.dumps(_req("ping")) + "\n"
        stdin = io.StringIO(msg)
        stdout = io.StringIO()
        mcp_server.serve(stdin=stdin, stdout=stdout)
        stdout.seek(0)
        resp = json.loads(stdout.read().strip())
        self.assertIn("result", resp)

    def test_serve_returns_parse_error_on_bad_json(self):
        import io
        stdin = io.StringIO("not json at all\n")
        stdout = io.StringIO()
        mcp_server.serve(stdin=stdin, stdout=stdout)
        stdout.seek(0)
        resp = json.loads(stdout.read().strip())
        self.assertIn("error", resp)
        self.assertEqual(resp["error"]["code"], -32700)


if __name__ == "__main__":
    unittest.main()

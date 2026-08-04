"""Minimal MCP (Model Context Protocol) server for stillworks.

Newline-delimited JSON-RPC 2.0 over stdio. Zero dependencies — this speaks
just enough MCP for coding agents (Claude Code, Codex, OpenCode, ...) to call
the four stillworks operations. Each tool shells out to the CLI in a fresh
subprocess so the served project code never loads into the server process.
"""

from __future__ import annotations

import json
import subprocess
import sys

PROTOCOL_VERSION = "2024-11-05"

TOOLS = [
    {
        "name": "stillworks_lock",
        "description": (
            "Record the current behavior of code as a baseline (lockfile). "
            "Give either a Python module/file plus --fuzz/--run options, or "
            "shell commands. Run this BEFORE changing code."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_dir": {"type": "string",
                                "description": "absolute path to the project"},
                "target": {"type": "string",
                           "description": "Python module (pkg.mod) or file path"},
                "fuzz": {"type": "integer",
                         "description": "inputs per annotated function"},
                "run": {"type": "string",
                        "description": "script to run and record"},
                "commands": {"type": "array", "items": {"type": "string"},
                             "description": "shell commands to record "
                                            "(any language)"},
            },
            "required": ["project_dir"],
        },
    },
    {
        "name": "stillworks_check",
        "description": (
            "Replay the recorded baseline against the current code and report "
            "OK/CHANGED/GONE per record. Run this AFTER changing code. "
            "Deterministic: executes the code, no LLM judgment."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_dir": {"type": "string"},
            },
            "required": ["project_dir"],
        },
    },
    {
        "name": "stillworks_accept",
        "description": (
            "Accept intentional behavior changes into the baseline. "
            "Pass record ids, or accept_all=true."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_dir": {"type": "string"},
                "ids": {"type": "array", "items": {"type": "string"}},
                "accept_all": {"type": "boolean"},
            },
            "required": ["project_dir"],
        },
    },
    {
        "name": "stillworks_report",
        "description": "Produce a markdown evidence report of the baseline, "
                       "last check, and accepted changes.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_dir": {"type": "string"},
            },
            "required": ["project_dir"],
        },
    },
]


def _run_cli(cli_args, project_dir):
    cmd = [sys.executable, "-m", "stillworks", "--project", project_dir] + cli_args
    # Our own CLI on the other end, writing UTF-8 — see cli.main.  Letting the
    # locale choose the codec here means an agent asking why a check failed
    # gets a decode error instead of the answer.
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=600)
    out = proc.stdout
    if proc.stderr:
        out = out + ("\n" if out else "") + proc.stderr
    return proc.returncode, out.strip()


def _tool_call(name, arguments):
    project_dir = arguments.get("project_dir")
    if not project_dir:
        return True, "missing required argument: project_dir"
    if name == "stillworks_lock":
        args = ["lock"]
        if arguments.get("target"):
            args.append(arguments["target"])
        if arguments.get("fuzz"):
            args += ["--fuzz", str(arguments["fuzz"])]
        if arguments.get("run"):
            args += ["--run", arguments["run"]]
        for c in arguments.get("commands") or []:
            args += ["--cmd", c]
        code, out = _run_cli(args, project_dir)
        return code != 0, out or "locked"
    if name == "stillworks_check":
        code, out = _run_cli(["check", "--json"], project_dir)
        # exit 1 = behavior changed: that's a result, not a tool error
        return code > 1, out
    if name == "stillworks_accept":
        args = ["accept"]
        if arguments.get("accept_all"):
            args.append("--all")
        args += arguments.get("ids") or []
        code, out = _run_cli(args, project_dir)
        return code != 0, out
    if name == "stillworks_report":
        code, out = _run_cli(["report"], project_dir)
        return code != 0, out
    return True, "unknown tool: {}".format(name)


def _response(req_id, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": req_id}
    if error is not None:
        msg["error"] = error
    else:
        msg["result"] = result
    return msg


def _handle(msg):
    method = msg.get("method")
    req_id = msg.get("id")
    if method == "initialize":
        return _response(req_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "stillworks", "version": _version()},
        })
    if method == "notifications/initialized":
        return None  # notification, no response
    if method == "ping":
        return _response(req_id, {})
    if method == "tools/list":
        return _response(req_id, {"tools": TOOLS})
    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name", "")
        arguments = params.get("arguments") or {}
        try:
            is_error, text = _tool_call(name, arguments)
        except Exception as exc:
            is_error, text = True, "{}: {}".format(type(exc).__name__, exc)
        return _response(req_id, {
            "content": [{"type": "text", "text": text}],
            "isError": bool(is_error),
        })
    if req_id is None:
        return None  # unknown notification: ignore
    return _response(req_id, error={"code": -32601,
                                    "message": "method not found: {}".format(method)})


def _version():
    from . import __version__
    return __version__


def serve(stdin=None, stdout=None):
    """Read newline-delimited JSON-RPC from stdin, write responses to stdout."""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            resp = _response(None, error={"code": -32700, "message": "parse error"})
            stdout.write(json.dumps(resp) + "\n")
            stdout.flush()
            continue
        resp = _handle(msg)
        if resp is not None:
            stdout.write(json.dumps(resp) + "\n")
            stdout.flush()
    return 0

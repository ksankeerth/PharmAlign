"""A logging pass-through between the agent and the server's MCP endpoint, for eval runs only.

The ADK agent keeps no record of what it searched (the server's agent_tool_log holds hashes). Put this between them to see
every tool call of a turn: the tool, its arguments, and a short view of what came back.

    .venv/bin/python eval/mcp_tap.py --listen 8320 --target http://127.0.0.1:8310 --log eval/runs/golden-mcp.jsonl
    # and start the agent with PHARMALIGN_MCP_URL=http://127.0.0.1:8320/mcp

One JSON line per tools/call: {"t": epoch seconds, "tool", "args", "ms", "hits", "result": first 400 chars of text}. Headers
(the run token) are passed on and never written. The log goes to eval/runs/, which is git-ignored.
"""
import argparse
import json
import time
from pathlib import Path

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse
from starlette.routing import Route

HOP = {"host", "content-length", "connection", "transfer-encoding", "keep-alive", "upgrade", "te", "trailer"}


def build(target: str, log: Path) -> Starlette:
    client = httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=5.0))

    def note(call: dict, started: float, text: str) -> None:
        result = text
        hits = None
        for line in text.splitlines():  # the reply is JSON or one SSE "data:" line
            line = line.removeprefix("data:").strip()
            if line.startswith("{"):
                try:
                    body = json.loads(line)
                except ValueError:
                    continue
                parts = (body.get("result") or {}).get("content") or []
                result = "".join(p.get("text", "") for p in parts if isinstance(p, dict)) or result
                structured = (body.get("result") or {}).get("structuredContent")
                if isinstance(structured, dict) and isinstance(structured.get("hits"), list):
                    hits = [h.get("anchor") for h in structured["hits"]]
                else:
                    try:
                        inner = json.loads(result)
                        if isinstance(inner, dict) and isinstance(inner.get("hits"), list):
                            hits = [h.get("anchor") for h in inner["hits"]]
                    except ValueError:
                        pass
                break
        record = {"t": started, "tool": call["name"], "args": call.get("arguments"), "ms": round((time.time() - started) * 1000),
                  "hits": hits, "result": result[:400]}
        with log.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    async def proxy(request: Request) -> Response:
        body = await request.body()
        call = None
        if request.method == "POST" and body:
            try:
                message = json.loads(body)
                if isinstance(message, dict) and message.get("method") == "tools/call":
                    call = message.get("params") or {}
            except ValueError:
                pass
        headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP | {"accept-encoding"}}
        upstream = client.build_request(request.method, target + request.url.path, params=request.query_params, headers=headers, content=body)
        started = time.time()
        reply = await client.send(upstream, stream=True)
        out_headers = {k: v for k, v in reply.headers.items() if k.lower() not in HOP | {"content-encoding"}}
        chunks: list[bytes] = []

        async def relay():
            try:
                async for chunk in reply.aiter_raw():
                    chunks.append(chunk)
                    yield chunk
            finally:
                await reply.aclose()
                if call is not None:
                    note(call, started, b"".join(chunks).decode("utf-8", "replace"))

        return StreamingResponse(relay(), status_code=reply.status_code, headers=out_headers)

    return Starlette(routes=[Route("/{path:path}", proxy, methods=["GET", "POST", "DELETE", "OPTIONS"])])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--listen", type=int, default=8320)
    ap.add_argument("--target", default="http://127.0.0.1:8310")
    ap.add_argument("--log", required=True)
    args = ap.parse_args()
    path = Path(args.log)
    path.parent.mkdir(parents=True, exist_ok=True)
    uvicorn.run(build(args.target.rstrip("/"), path), host="127.0.0.1", port=args.listen, log_level="warning")

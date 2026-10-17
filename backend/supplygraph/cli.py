"""Command-line entry point: ``supplygraph {import,serve,mcp,ask,tool}``."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from .config import get_settings
from .logging_setup import configure_logging


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="supplygraph", description=__doc__)
    parser.add_argument("--workspace", help="workspace id (default: SUPPLYGRAPH_DEFAULT_TENANT)")
    sub = parser.add_subparsers(dest="command", required=True)

    imp = sub.add_parser("import", help="import a directory of CSV datasets")
    imp.add_argument("directory", help="folder with items.csv, bom.csv, sourcing.csv, ...")
    imp.add_argument("--mode", choices=("merge", "replace"), default="merge")
    imp.add_argument("--dry-run", action="store_true", help="validate only")

    serve = sub.add_parser("serve", help="run the HTTP API locally")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)

    sub.add_parser("mcp", help="run the MCP server on stdio")

    ask = sub.add_parser("ask", help="ask the agent a question")
    ask.add_argument("question")

    tool = sub.add_parser("tool", help="call one graph tool directly (no LLM)")
    tool.add_argument("name")
    tool.add_argument("arguments", nargs="?", default="{}", help="JSON object")

    args = parser.parse_args(argv)
    settings = get_settings()
    workspace = args.workspace or settings.default_tenant

    if args.command == "serve":
        import uvicorn

        from .api import create_app

        uvicorn.run(create_app(settings), host=args.host, port=args.port, log_config=None)
        return 0
    if args.command == "mcp":
        from .mcp_server import serve_stdio

        asyncio.run(serve_stdio(settings))
        return 0

    configure_logging(settings.log_level, as_json=False)
    return asyncio.run(_run(args, settings, workspace))


async def _run(args: argparse.Namespace, settings, workspace: str) -> int:
    from .db import GraphStore

    if args.command == "import":
        from .ingest import ingest, load_dataset_dir

        outcome = load_dataset_dir(args.directory, base_currency=settings.base_currency)
        for issue in outcome.issues:
            where = ":".join(str(x) for x in (issue.file, issue.row, issue.column) if x)
            print(f"{issue.severity:7} {where} {issue.message}", file=sys.stderr)
        print(json.dumps({"files": outcome.files, "rows": outcome.rows}, indent=2))
        if not outcome.ok or outcome.network is None:
            return 2
        if args.dry_run:
            return 0

    store = GraphStore(settings)
    try:
        if args.command == "import":
            counts = await ingest(store, workspace, outcome.network, mode=args.mode)
            print(json.dumps({"graph": store.graph_id(workspace), "written": counts}, indent=2))
            return 0

        if args.command == "tool":
            from .llm import build_provider
            from .registry import ToolCallError, call_tool
            from .tools import toolbox_for

            try:
                arguments = json.loads(args.arguments)
                provider = build_provider(settings)
                toolbox = toolbox_for(settings, store, workspace, provider=provider)
                result = await call_tool(toolbox, args.name, arguments)
            except (json.JSONDecodeError, ToolCallError) as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 2
            print(result.model_dump_json(indent=2))
            return 0

        if args.command == "ask":
            from .agent import SupplyGraphAgent
            from .catalog import CatalogCache

            agent = SupplyGraphAgent(settings, store, CatalogCache(store))
            answer = await agent.ask(args.question, tenant=workspace)
            print(answer.answer)
            print("\n--- trace ---", file=sys.stderr)
            for t in answer.tool_calls:
                status = t.error or f"{t.outcome}, {t.rows} rows"
                print(
                    f"{t.tool}({json.dumps(t.arguments)}) -> {status} [{t.elapsed_ms} ms]",
                    file=sys.stderr,
                )
            if not answer.grounded:
                print(f"grounding: replaced ({'; '.join(answer.violations)})", file=sys.stderr)
            return 0
    finally:
        await store.close()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

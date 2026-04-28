"""CLI entry point."""

from __future__ import annotations

import argparse
import os
import sys

import uvicorn


def main() -> None:
    p = argparse.ArgumentParser(prog="datachat")
    sub = p.add_subparsers(dest="cmd")

    serve = sub.add_parser("serve", help="Run the HTTP API.")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", default=8080, type=int)
    serve.add_argument("--data-dir", default=None)

    args = p.parse_args()
    if args.cmd == "serve":
        if args.data_dir:
            os.environ["DATACHAT_DATA_DIR"] = args.data_dir
        uvicorn.run("app.api.app:build_app", factory=True,
                    host=args.host, port=args.port, reload=False)
    else:
        p.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()

"""Application entrypoint for running via waitress or development server."""

import argparse
import sys
from waitress import serve
from web import create_app


def main():
    """Parse CLI arguments and start Waitress server."""
    parser = argparse.ArgumentParser(description="Christmas Gift Exchange Web Application")
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host interface to bind (default: 127.0.0.1)"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=13046,
        help="Port number to bind (default: 13046)"
    )
    args = parser.parse_args()

    app = create_app()
    print(f"Starting Christmas Gift Exchange on http://{args.host}:{args.port}/christmas/")
    sys.stdout.flush()

    serve(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()

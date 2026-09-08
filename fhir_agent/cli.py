import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from dotenv import load_dotenv
from .auth import BackendAuth
from .config import Settings
from .client import FHIRClient
from .errors import ConfigurationError, FHIRError
from .signing import public_jwks, signer_from_settings


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only Epic FHIR sandbox client")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("auth-check", help="Verify authentication without printing the token")
    sub.add_parser("jwks", help="Print ONLY the configured key's public JWK Set")
    get = sub.add_parser("get", help="Read a resource or a single search page")
    get.add_argument("path")
    get.add_argument("--param", action="append", default=[], metavar="KEY=VALUE")
    search = sub.add_parser("search", help="Search with pagination")
    search.add_argument("path")
    search.add_argument("--param", action="append", default=[], metavar="KEY=VALUE")
    search.add_argument("--max-pages", type=int, default=20)
    search.add_argument("--with-metadata", action="store_true",
                        help="Return pages with match/include/outcome modes and source metadata")
    args = parser.parse_args(argv)
    load_dotenv(Path.cwd() / ".env")
    try:
        settings = Settings.from_env()
        if args.command == "jwks":
            result = public_jwks(signer_from_settings(settings), settings.key_id)
        elif args.command == "auth-check":
            auth = BackendAuth(settings)
            try:
                auth.token()
                result = {"authenticated": True, "granted_scopes": auth.granted_scopes}
            finally:
                auth.close()
        else:
            params = []
            for item in args.param:
                if "=" not in item:
                    raise ConfigurationError("Each --param must use KEY=VALUE.")
                params.append(tuple(item.split("=", 1)))
            with FHIRClient(settings) as client:
                if args.command == "search":
                    result = (asdict(client.search_result(args.path, params, args.max_pages))
                              if args.with_metadata else list(client.search(args.path, params, args.max_pages)))
                else:
                    result = client.get(args.path, params)
        print(json.dumps(result, indent=2))
    except Exception as exc:
        # Avoid printing remote payloads, assertions, tokens, or patient data in errors.
        message = str(exc) if isinstance(exc, (ConfigurationError, FHIRError)) else type(exc).__name__
        print(f"Request could not complete: {message}", file=sys.stderr)
        return 1
    return 0

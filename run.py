#!/usr/bin/env python3
"""Single-instance startup with literal, non-executing environment-file loading."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import shlex


ROOT = Path(__file__).resolve().parent


def load_env_file(path: Path, environ=None) -> int:
    """Load literal KEY=value; process environment wins. Never execute a shell."""
    environ = os.environ if environ is None else environ
    parsed = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ValueError(f"Invalid environment entry at line {number}; expected KEY=value")
        value = value.strip()
        if value.startswith(("'", '"')):
            try:
                parts = shlex.split(value, comments=True, posix=True)
            except ValueError:
                raise ValueError(f"Invalid quoted environment value at line {number}") from None
            if len(parts) != 1:
                raise ValueError(f"Unexpected text after quoted value at line {number}")
            value = parts[0]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].rstrip()
        if "\x00" in value:
            raise ValueError(f"Invalid environment value at line {number}")
        parsed[key] = value
    for key, value in parsed.items():
        environ.setdefault(key, value)
    return len(parsed)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Start the PaperStudio web service (one process)")
    parser.add_argument("--host", help="Overrides PAPERSTUDIO_HOST; default 127.0.0.1")
    parser.add_argument("--port", type=int, help="Overrides PAPERSTUDIO_PORT; default 8000")
    parser.add_argument("--env-file", type=Path, help="Literal KEY=value file; defaults to repository .env")
    parser.add_argument("--no-env", action="store_true", help="Use the process environment only")
    parser.add_argument("--no-demo", action="store_true", help="Do not seed the self-authored PDF fixture")
    parser.add_argument("--check-config", action="store_true", help="Validate environment syntax without starting services")
    args = parser.parse_args(argv)
    if args.no_env and args.env_file:
        parser.error("--env-file and --no-env cannot be combined")
    try:
        if not args.no_env:
            env_path = args.env_file or ROOT / ".env"
            if env_path.is_file():
                load_env_file(env_path)
            elif args.env_file:
                parser.error("The requested environment file is not readable")
        host = args.host or os.environ.get("PAPERSTUDIO_HOST", "127.0.0.1")
        try:
            port = args.port if args.port is not None else int(os.environ.get("PAPERSTUDIO_PORT", "8000"))
        except ValueError:
            raise ValueError("PAPERSTUDIO_PORT must be an integer") from None
        if not host.strip() or not 1 <= port <= 65535:
            raise ValueError("Host must be nonempty and port must be between 1 and 65535")
    except (OSError, UnicodeError, ValueError) as error:
        parser.error(str(error) if isinstance(error, ValueError) and not isinstance(error, UnicodeError)
                     else "Unable to read environment configuration")
    if args.no_demo:
        os.environ["PAPERSTUDIO_SEED_DEMO"] = "0"
    configured_data = os.environ.get("PAPERSTUDIO_DATA_DIR")
    if configured_data and not Path(configured_data).is_absolute():
        os.environ["PAPERSTUDIO_DATA_DIR"] = str((ROOT / configured_data).resolve())
    if args.check_config:
        print("Configuration syntax is valid. No services started; no secrets displayed.")
        return
    import uvicorn
    # One process owns SQLite queues. Reload/multiple workers would duplicate
    # background worker sets and are intentionally not exposed as launch flags.
    uvicorn.run("app.main:app", host=host, port=port, workers=1,
                app_dir=str(ROOT), proxy_headers=True,
                forwarded_allow_ips=os.environ.get("PAPERSTUDIO_TRUSTED_PROXIES", "127.0.0.1"))


if __name__ == "__main__":
    main()


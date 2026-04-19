"""Command-line interface for Herbert."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from herbert import __version__
from herbert.config import DEFAULT_CONFIG_PATH, HerbertConfig, load_config
from herbert.events import AsyncEventBus
from herbert.logging import setup_logging
from herbert.secrets import MissingSecretError, ensure_frontend_bearer_token, load_secrets


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.subcommand is None:
        parser.print_help()
        return 2

    cli_config = Path(args.config) if args.config else None
    cfg = load_config(cli_path=cli_config)
    logger = setup_logging(log_path=cfg.log_path, level=cfg.logging.level)

    if args.subcommand == "dev":
        return _run_dev(cfg, args, logger)
    if args.subcommand == "run":
        return _run_production(cfg, args, logger)
    return 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="herbert",
        description="Herbert — a Claude-powered voice assistant device.",
    )
    parser.add_argument("--version", action="version", version=f"herbert {__version__}")
    parser.add_argument(
        "--config",
        help=f"Path to config TOML (default: $HERBERT_CONFIG, then {DEFAULT_CONFIG_PATH})",
    )

    subs = parser.add_subparsers(dest="subcommand")

    dev = subs.add_parser("dev", help="Development mode: macOS-friendly, keyboard activation, no kiosk")
    dev.add_argument("--no-boot", action="store_true", help="Skip the fake boot sequence")
    dev.add_argument("--expose", action="store_true", help="Expose the frontend on the LAN (requires bearer token)")

    run = subs.add_parser("run", help="Production mode: Pi 5 with GPIO + auto-launched kiosk")
    run.add_argument("--expose", action="store_true", help="Expose the frontend on the LAN (requires bearer token)")

    return parser


def _run_dev(cfg: HerbertConfig, args: argparse.Namespace, logger: logging.Logger) -> int:
    logger.info("herbert dev starting, version=%s", __version__)
    _check_required_secrets(cfg, logger)
    if args.expose:
        token = ensure_frontend_bearer_token(cfg.secrets_path)
        logger.info("frontend exposed on LAN; bearer token last-4 = %s", token[-4:])
    logger.info(
        "herbert %s ready (dev mode) — config %s, log %s",
        __version__,
        _config_source(args.config),
        cfg.log_path,
    )
    return _run_daemon(cfg, expose=args.expose)


def _run_production(cfg: HerbertConfig, args: argparse.Namespace, logger: logging.Logger) -> int:
    logger.info("herbert run starting, version=%s", __version__)
    _check_required_secrets(cfg, logger)
    token = ensure_frontend_bearer_token(cfg.secrets_path)
    if args.expose:
        logger.info("frontend exposed on LAN; bearer token last-4 = %s", token[-4:])
    logger.info(
        "herbert %s ready (pi production mode) — config %s, log %s",
        __version__,
        _config_source(args.config),
        cfg.log_path,
    )
    return _run_daemon(cfg, expose=args.expose)


def _run_daemon(cfg: HerbertConfig, *, expose: bool = False) -> int:
    from herbert.daemon import build_and_run

    bus = AsyncEventBus()
    # Reattach logging with the bus so LogLine events flow to /ws in Unit 8+
    setup_logging(log_path=cfg.log_path, level=cfg.logging.level, bus=bus)
    try:
        return asyncio.run(build_and_run(cfg, bus=bus, expose=expose))
    except KeyboardInterrupt:
        return 0


def _check_required_secrets(cfg: HerbertConfig, logger: logging.Logger) -> None:
    """Fail closed if required provider keys are missing for the configured providers."""
    store = load_secrets(cfg.secrets_path)
    required: list[str] = []
    # LLM is always required
    required.append("ANTHROPIC_API_KEY")
    # TTS default is ElevenLabs
    if cfg.tts.provider == "elevenlabs":
        required.append("ELEVENLABS_API_KEY")
    missing = [k for k in required if store.get(k) is None]
    if missing:
        logger.error(
            "fail-closed: required secrets missing from %s: %s. "
            "Add them (one per line, KEY=value) and chmod 0600.",
            cfg.secrets_path,
            ", ".join(missing),
        )
        raise MissingSecretError(f"missing secrets: {', '.join(missing)}")


def _config_source(cli_config: str | None) -> str:
    if cli_config:
        return cli_config
    import os

    return os.environ.get("HERBERT_CONFIG") or str(DEFAULT_CONFIG_PATH)


if __name__ == "__main__":
    sys.exit(main())

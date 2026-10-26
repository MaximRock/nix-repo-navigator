"""Logging setup shared by the CLI and the MCP server.

Console output honours ``Config.log_level`` (stderr, so stdio MCP transport
stays clean). Parser failures additionally land in ``parse_errors.log`` next
to the graph DB, as required by spec v3 §"Изоляция ошибок".
"""

from __future__ import annotations

import logging
from pathlib import Path

PARSE_LOG_NAME = "parse_errors.log"
_PARSER_LOGGER = "repo_navigator.parsers"
_FILE_HANDLER_MARK = "_repo_navigator_parse_log"


def configure_logging(level: str, log_dir: Path, reset: bool = False) -> Path:
    """Configure console logging; return the parse-error log path.

    ``reset`` truncates the parse log so it reflects the current index run.
    """
    logging.basicConfig(
        level=level.upper(),
        format="%(levelname)s %(name)s: %(message)s",
    )
    log_dir.mkdir(parents=True, exist_ok=True)
    error_log = log_dir / PARSE_LOG_NAME

    logger = logging.getLogger(_PARSER_LOGGER)
    for handler in list(logger.handlers):
        if getattr(handler, _FILE_HANDLER_MARK, False):
            logger.removeHandler(handler)
            handler.close()
    if reset:
        error_log.write_text("", encoding="utf-8")
    handler = logging.FileHandler(error_log, encoding="utf-8", delay=True)
    setattr(handler, _FILE_HANDLER_MARK, True)
    handler.setLevel(logging.WARNING)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    logger.addHandler(handler)
    return error_log

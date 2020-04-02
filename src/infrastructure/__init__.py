import sys

from infrastructure._version import version as __version__
from infrastructure.strings import PLUGIN_NAME
from loguru import logger

config = {
    "handlers": [
        {
            "sink": sys.stdout,
            "format": "<green>{thread.name: <25} | {function: ^30} | {line: >3}</green> | {message}",
            "colorize": True,
            "enqueue": True,
            "backtrace": True,
            "diagnose": True,
        }
    ]
}
logger.configure(**config)
logger.enable(PLUGIN_NAME)


__all__ = ["__version__", logger]

"""Runtime and strict-mypy consumer smoke check, run outside the checkout."""

import logging
from importlib.metadata import distribution
from pathlib import Path

import structlog
from sentry_sdk import Scope
from structlog.types import EventDict, Processor

import sentry_structlog
from sentry_structlog import SentryProcessor

package = distribution("sentry-structlog")
installed_module = Path(
    str(package.locate_file("sentry_structlog/__init__.py"))
).resolve()
assert Path(sentry_structlog.__file__).resolve() == installed_module
assert "site-packages" in installed_module.parts
assert Path(str(package.locate_file("sentry_structlog/py.typed"))).is_file()

processor = SentryProcessor(
    event_level=logging.ERROR,
    tag_keys=["request_id"],
    ignore_loggers=["ignored"],
    scope=Scope(),
)
processors: list[Processor] = [structlog.stdlib.add_log_level, processor]
structlog.configure(processors=processors)
event: EventDict = {"event": "wheel smoke", "level": "info", "sentry_skip": True}
result: EventDict = processor(logging.getLogger(__name__), "info", event)
assert result["event"] == "wheel smoke"
print(f"Installed wheel import and consumer passed: {installed_module}")

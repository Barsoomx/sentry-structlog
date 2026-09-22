"""Runtime and strict-mypy consumer smoke check, run outside the checkout."""

from __future__ import annotations

import logging
from importlib.metadata import distribution
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from sentry_sdk import Client, isolation_scope
from structlog.types import EventDict, Processor

import sentry_structlog
from sentry_structlog import RESERVED_TAG_KEYS, SentryProcessor

if TYPE_CHECKING:
    from sentry_sdk._types import Event, Hint
    from typing_extensions import assert_type

package = distribution("sentry-structlog")
installed_module = Path(
    str(package.locate_file("sentry_structlog/__init__.py"))
).resolve()
assert Path(sentry_structlog.__file__).resolve() == installed_module
assert "site-packages" in installed_module.parts
assert Path(str(package.locate_file("sentry_structlog/py.typed"))).is_file()

processor = SentryProcessor(
    event_level=logging.ERROR,
    tag_keys=("request_id", "password", "excluded"),
    exclude_tag_keys=("excluded",),
    scrub=True,
    ignore_loggers=["ignored"],
)
SentryProcessor(tag_keys="__all__", exclude_tag_keys=RESERVED_TAG_KEYS, scrub=False)
if TYPE_CHECKING:
    assert_type(processor, SentryProcessor)
    assert_type(processor.event_level, int)
    assert_type(RESERVED_TAG_KEYS, frozenset[str])

processors: list[Processor] = [structlog.stdlib.add_log_level, processor]
structlog.configure(
    processors=processors, logger_factory=structlog.ReturnLoggerFactory()
)
event: EventDict = {"event": "wheel smoke", "level": "info", "sentry_skip": True}
result: EventDict = processor(logging.getLogger(__name__), "info", event)
assert result["event"] == "wheel smoke"

captured: list[Event] = []


def before_send(event: Event, hint: Hint) -> Event | None:
    original: EventDict = hint["structlog"]
    assert original["request_id"] == "wheel-consumer"
    assert original["password"] == "example"
    assert "tags" in event
    assert event["tags"] == {"request_id": "wheel-consumer"}
    assert "contexts" in event
    assert event["contexts"]["structlog"]["password"] == "[Filtered]"
    captured.append(event)
    return None


with Client(
    dsn="https://public@example.invalid/1",
    default_integrations=False,
    before_send=before_send,
    transport=lambda event: None,
) as client:
    with isolation_scope() as scope:
        scope.set_client(client)
        structlog.get_logger().error(
            "wheel smoke",
            request_id="wheel-consumer",
            password="example",
            excluded="example",
        )

assert len(captured) == 1
assert "event" in RESERVED_TAG_KEYS
print(f"Installed wheel import and consumer passed: {installed_module}")

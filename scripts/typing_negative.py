"""Static-only API rejection checks; unused ignores must fail strict mypy."""

from __future__ import annotations

from typing import TYPE_CHECKING

import sentry_sdk

from sentry_structlog import SentryProcessor

if TYPE_CHECKING:
    from sentry_sdk._types import Event


def wrong_before_send(event: Event) -> Event:
    return event


SentryProcessor(tag_keys=123)  # type: ignore[arg-type]
SentryProcessor(scrub="yes")  # type: ignore[arg-type]
SentryProcessor(exclude_tag_keys=5)  # type: ignore[arg-type]
sentry_sdk.init(before_send=wrong_before_send)  # type: ignore[arg-type]

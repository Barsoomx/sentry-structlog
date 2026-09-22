from __future__ import annotations

import logging
import re
import sys
from decimal import Decimal
from enum import Enum
from fnmatch import fnmatch
from typing import Any, Optional
from collections.abc import MutableMapping, Iterable
from uuid import UUID

from sentry_sdk import Scope, get_isolation_scope
from sentry_sdk.integrations.logging import _IGNORED_LOGGERS
from sentry_sdk.utils import capture_internal_exceptions, event_from_exception
from structlog.types import EventDict, ExcInfo, WrappedLogger


RESERVED_TAG_KEYS = frozenset(
    {
        "event",
        "level",
        "logger",
        "timestamp",
        "exc_info",
        "exception",
        "stack",
        "stack_info",
        "sentry_skip",
        "sentry",
        "sentry_id",
        "_record",
        "_from_structlog",
    }
)
_TAG_KEY_PATTERN = re.compile(r"[a-zA-Z0-9_.:-]{1,32}")


def _to_tag_value(value: Any) -> str | None:
    if not isinstance(value, (str, int, float, bool, UUID, Decimal, Enum)):
        return None
    value = str(value)
    if len(value) > 200 or "\n" in value:
        return None
    return value


def _copy_scrub_data(value: Any) -> Any:
    """Copy containers the scrubber mutates, leaving opaque log values untouched."""
    if isinstance(value, dict):
        return {key: _copy_scrub_data(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_copy_scrub_data(item) for item in value]
    return value


def _figure_out_exc_info(v: Any) -> ExcInfo:
    """
    Depending on the Python version will try to do the smartest thing possible
    to transform *v* into an ``exc_info`` tuple.
    """
    if isinstance(v, BaseException):
        return (v.__class__, v, v.__traceback__)
    elif isinstance(v, tuple):
        return v  # type: ignore
    elif v:
        return sys.exc_info()  # type: ignore

    return v


class SentryProcessor:
    """Sentry processor for structlog.

    Uses Sentry SDK to capture events in Sentry.
    """

    def __init__(
        self,
        level: int = logging.INFO,
        event_level: int = logging.WARNING,
        active: bool = True,
        as_context: bool = True,
        ignore_breadcrumb_data: Iterable[str] = (
            "level",
            "logger",
            "event",
            "timestamp",
        ),
        tag_keys: Iterable[str] | str | None = None,
        ignore_loggers: Iterable[str] | None = None,
        verbose: bool = False,
        scope: Scope | None = None,
        exclude_tag_keys: Iterable[str] = (),
        scrub: bool = True,
    ) -> None:
        """
        :param level: Events of this or higher levels will be reported as
            Sentry breadcrumbs. Dfault is :obj:`logging.INFO`.
        :param event_level: Events of this or higher levels will be reported to Sentry
            as events. Default is :obj:`logging.WARNING`.
        :param active: A flag to make this processor enabled/disabled.
        :param as_context: Send `event_dict` as extra info to Sentry.
            Default is :obj:`True`.
        :param ignore_breadcrumb_data: A list of data keys that will be excluded from
            breadcrumb data. Defaults to keys which are already sent separately.
        :param tag_keys: An iterable of keys to send as tags, or `"__all__"` for all
            non-reserved keys. Only scalar values and valid Sentry keys are sent.
            Any other string raises :obj:`ValueError`.
        :param ignore_loggers: A list of logger names to ignore any events from.
        :param verbose: Report the action taken by the logger in the `event_dict`.
            Default is :obj:`False`.
        :param scope: Optionally specify :obj:`sentry_sdk.Scope`.
        :param exclude_tag_keys: Additional keys to exclude from tags in either mode.
        :param scrub: Scrub context and drop sensitive tags using the client's event
            scrubber. Default is :obj:`True`. Breadcrumb scrubbing is left to the SDK.
        """
        self.event_level = event_level
        self.level = level
        self.active = active
        if isinstance(tag_keys, str):
            if tag_keys != "__all__":
                raise ValueError('tag_keys must be "__all__" or an iterable of keys')
            self.tag_keys = tag_keys
        else:
            self.tag_keys = frozenset(tag_keys) if tag_keys is not None else None
        self.exclude_tag_keys = frozenset(exclude_tag_keys)
        self.scrub = scrub
        self.verbose = verbose

        self._scope = scope
        self._as_context = as_context
        self.ignore_breadcrumb_data = ignore_breadcrumb_data

        self._ignored_loggers: set[str] = set()
        if ignore_loggers is not None:
            self._ignored_loggers.update(set(ignore_loggers))

    @staticmethod
    def _get_logger_name(
        logger: WrappedLogger, event_dict: MutableMapping[str, Any]
    ) -> Optional[str]:
        """Get logger name from event_dict with a fallbacks to logger.name and
        record.name

        :param logger: logger instance
        :param event_dict: structlog event_dict
        """
        record = event_dict.get("_record")
        l_name = event_dict.get("logger")
        logger_name = None

        if l_name:
            logger_name = l_name
        elif record and hasattr(record, "name"):
            logger_name = record.name

        if not logger_name and logger and hasattr(logger, "name"):
            logger_name = logger.name

        return logger_name

    def _get_scope(self) -> Scope:
        return self._scope or get_isolation_scope()

    def _get_event_and_hint(
        self, event_dict: EventDict, original_event_dict: EventDict | None = None
    ) -> tuple[dict, dict]:
        """Create a sentry event and hint from structlog `event_dict` and sys.exc_info.

        :param event_dict: structlog event_dict
        :param original_event_dict: snapshot of `event_dict` taken after removing
            `sentry_skip`, before capturing the event; used for tags and context.
            Defaults to `event_dict` itself.
        """
        if original_event_dict is None:
            original_event_dict = event_dict

        exc_info = _figure_out_exc_info(event_dict.get("exc_info", None))
        has_exc_info = exc_info and exc_info != (None, None, None)

        if has_exc_info:
            client = self._get_scope().get_client()
            options: dict[str, Any] = client.options if client else {}
            event, hint = event_from_exception(
                exc_info,
                client_options=options,
            )
        else:
            event, hint = {}, {}

        event["message"] = event_dict.get("event")
        event["level"] = event_dict.get("level")
        if "logger" in event_dict:
            event["logger"] = event_dict["logger"]

        scrubber = (
            self._get_scope().get_client().options.get("event_scrubber")
            if self.scrub
            else None
        )
        if self._as_context:
            context = (
                _copy_scrub_data(original_event_dict)
                if scrubber is not None and scrubber.recursive
                else dict(original_event_dict)
            )
            if scrubber is not None:
                scrubber.scrub_dict(context)
            event["contexts"] = {"structlog": context}
        if self.tag_keys is not None:
            tags = {}
            for key, value in original_event_dict.items():
                if self.tag_keys == "__all__":
                    if key in RESERVED_TAG_KEYS:
                        continue
                elif key not in self.tag_keys:
                    continue
                if key in self.exclude_tag_keys:
                    continue
                if not isinstance(key, str) or not _TAG_KEY_PATTERN.fullmatch(key):
                    continue
                if scrubber is not None and key.lower() in scrubber.denylist:
                    continue
                tag_value = _to_tag_value(value)
                if tag_value is not None:
                    tags[key] = tag_value
            event["tags"] = tags

        return event, hint

    def _get_breadcrumb_and_hint(self, event_dict: EventDict) -> tuple[dict, dict]:
        data = {
            k: v for k, v in event_dict.items() if k not in self.ignore_breadcrumb_data
        }
        event = {
            "type": "log",
            "level": event_dict.get("level"),  # type: ignore
            "category": event_dict.get("logger"),
            "message": event_dict["event"],
            "timestamp": event_dict.get("timestamp"),
            "data": data,
        }

        return event, {"log_record": event_dict}

    def _can_record(self, logger: WrappedLogger, event_dict: EventDict) -> bool:
        logger_name = self._get_logger_name(logger=logger, event_dict=event_dict)
        if logger_name:
            for ignored_logger in _IGNORED_LOGGERS | self._ignored_loggers:
                if fnmatch(logger_name, ignored_logger):  # type: ignore
                    if self.verbose:
                        event_dict["sentry"] = "ignored"
                    return False
        return True

    def _handle_event(
        self, event_dict: EventDict, original_event_dict: EventDict | None = None
    ) -> None:
        with capture_internal_exceptions():
            event, hint = self._get_event_and_hint(event_dict, original_event_dict)
            sid = self._get_scope().capture_event(event, hint=hint)
            if sid:
                event_dict["sentry_id"] = sid
            if self.verbose:
                event_dict["sentry"] = "sent"

    def _handle_breadcrumb(self, event_dict: EventDict) -> None:
        with capture_internal_exceptions():
            event, hint = self._get_breadcrumb_and_hint(event_dict)
            self._get_scope().add_breadcrumb(event, hint=hint)

    @staticmethod
    def _get_level_value(level_name: str) -> int:
        """Get numeric value for the log level name given."""
        try:
            # Try to get one of predefined log levels
            return getattr(logging, level_name)
        except AttributeError as e:
            # May be it is a custom log level?
            level = logging.getLevelName(level_name)
            if isinstance(level, int):
                return level

            # Re-raise original error
            raise ValueError(f"{level_name} is not a valid log level") from e

    def __call__(
        self, logger: WrappedLogger, name: str, event_dict: EventDict
    ) -> EventDict:
        """A middleware to process structlog `event_dict` and send it to Sentry."""
        sentry_skip = event_dict.pop("sentry_skip", False)

        if self.active and not sentry_skip:
            level = self._get_level_value(event_dict["level"].upper())

            if self._can_record(logger, event_dict):
                original_event_dict = event_dict
                if level >= self.event_level:
                    original_event_dict = dict(event_dict)
                    self._handle_event(event_dict, original_event_dict)

                if level >= self.level:
                    self._handle_breadcrumb(original_event_dict)

        if self.verbose:
            event_dict.setdefault("sentry", "skipped")

        return event_dict

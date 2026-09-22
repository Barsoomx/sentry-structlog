import logging
import sys
import threading
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from uuid import UUID

import pytest
import sentry_sdk
import structlog
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.scrubber import EventScrubber

from sentry_structlog import SentryProcessor

INTEGRATIONS = [
    LoggingIntegration(event_level=None, level=None),
]

# Register custom log level
CUSTOM_LOG_LEVEL_NAME = "CuStOm_LeVeL"
CUSTOM_LOG_LEVEL_VALUE = logging.DEBUG
logging.addLevelName(CUSTOM_LOG_LEVEL_VALUE, CUSTOM_LOG_LEVEL_NAME)


@dataclass
class ClientParams:
    include_local_variables: bool = True
    include_source_context: bool = True
    max_value_length: int = 1024
    attach_stacktrace: bool = False
    event_scrubber: EventScrubber = field(default_factory=EventScrubber)

    @classmethod
    def from_request(cls, request):
        if not hasattr(request, "param"):
            return cls()

        if isinstance(request.param, dict):
            return cls(**request.param)

        if isinstance(request.param, cls):
            return request.param

        return cls()


class CaptureTransport(sentry_sdk.Transport):
    def __init__(self):
        super().__init__()
        self.events = []

    def capture_envelope(self, envelope):
        event = envelope.get_event()
        if event is not None:
            self.events.append(event)


@pytest.fixture
def sentry_events(request):
    params = ClientParams.from_request(request)
    transport = CaptureTransport()
    client = sentry_sdk.Client(
        transport=transport,
        integrations=INTEGRATIONS,
        auto_enabling_integrations=False,
        include_local_variables=params.include_local_variables,
        include_source_context=params.include_source_context,
        max_value_length=params.max_value_length,
        attach_stacktrace=params.attach_stacktrace,
        event_scrubber=params.event_scrubber,
    )

    with sentry_sdk.isolation_scope() as scope:
        scope.set_client(client)
        yield transport.events


def assert_event_dict(
    event_data, sentry_events, number_of_events=1, error=None, sentry_level=None
):
    assert len(sentry_events) == number_of_events
    assert (sentry_level or event_data["level"]) == sentry_events[0]["level"]
    assert event_data["event"] == sentry_events[0]["message"]

    if error is not None:
        assert sentry_events[0]["exception"]["values"][0]["type"] == error.__name__


class MockLogger:
    def __init__(self, name):
        self.name = name


def test_sentry_disabled():
    processor = SentryProcessor(active=False, verbose=True)
    event_dict = processor(None, None, {"level": "error"})
    assert event_dict.get("sentry") != "sent"


def test_sentry_skip():
    processor = SentryProcessor(verbose=True)
    event_dict = processor(None, None, {"sentry_skip": True, "level": "error"})
    assert event_dict.get("sentry") == "skipped"


def test_sentry_sent():
    processor = SentryProcessor(verbose=True)
    event_dict = processor(None, None, {"level": "error"})
    assert event_dict.get("sentry") == "sent"


@pytest.mark.parametrize(
    "level, level_value, sentry_level",
    [
        (CUSTOM_LOG_LEVEL_NAME, CUSTOM_LOG_LEVEL_VALUE, "debug"),
        ("debug", logging.DEBUG, "debug"),
        ("info", logging.INFO, "info"),
        ("warning", logging.WARNING, "warning"),
    ],
)
def test_sentry_log(sentry_events, level, level_value, sentry_level):
    event_data = {"level": level, "event": level + " message"}

    processor = SentryProcessor(event_level=level_value)
    processor(None, None, event_data)

    assert_event_dict(event_data, sentry_events, sentry_level=sentry_level)


@pytest.fixture
def custom_level_names(monkeypatch):
    monkeypatch.setattr(logging, "_nameToLevel", logging._nameToLevel.copy())
    monkeypatch.setattr(logging, "_levelToName", logging._levelToName.copy())
    logging.addLevelName(25, "LeVeL")
    logging.addLevelName(25, "NOTICE")
    # Standard structlog names take precedence over logging registrations.
    logging.addLevelName(45, "WARNING")


@pytest.mark.usefixtures("custom_level_names")
@pytest.mark.parametrize("threshold_offset", [0, 1])
@pytest.mark.parametrize(
    "level_fields, numeric_level, severity",
    [
        ({"level": CUSTOM_LOG_LEVEL_NAME}, CUSTOM_LOG_LEVEL_VALUE, "debug"),
        ({"level": "LeVeL"}, 25, "info"),
        ({"level": "NOTICE"}, 25, "info"),
        ({"level": "notice"}, 25, "info"),
        ({"level": "exception"}, 40, "error"),
        ({"level": "ExCePtIoN"}, 40, "error"),
        ({"level": "warn"}, 30, "warning"),
        ({"level": "warning"}, 30, "warning"),
        ({"level": "WARNING"}, 30, "warning"),
        ({"level": "critical"}, 50, "fatal"),
        ({"level": "fatal"}, 50, "fatal"),
        ({"level": "nonsense", "level_number": 40}, 40, "error"),
        ({"level": "debug", "level_number": 40}, 40, "error"),
        ({"level": "error", "level_number": 10}, 10, "debug"),
        ({"level_number": 25}, 25, "info"),
        ({"level": "basic_format", "level_number": 30}, 30, "warning"),
        ({"level": None, "level_number": 40}, 40, "error"),
        ({"level": "warning", "level_number": "40"}, 30, "warning"),
        ({"level": "warning", "level_number": 40.0}, 30, "warning"),
    ],
)
def test_level_resolution_filters_and_normalizes_payloads(
    sentry_events, level_fields, numeric_level, severity, threshold_offset
):
    original = {"event": "first", **level_fields}
    event_data = original.copy()
    threshold = numeric_level + threshold_offset
    processor = SentryProcessor(level=threshold, event_level=threshold)

    assert processor(None, None, event_data) is event_data
    assert {
        key: value for key, value in event_data.items() if key != "sentry_id"
    } == original
    if threshold_offset:
        assert not sentry_events
    else:
        [event] = sentry_events
        assert event["level"] == severity
        assert event["contexts"]["structlog"] == original

    # A subsequent event exposes any breadcrumb left by the first call.
    SentryProcessor()(None, None, {"level": "error", "event": "second"})
    breadcrumbs = sentry_events[-1].get("breadcrumbs", {}).get("values", [])
    if threshold_offset:
        assert breadcrumbs == []
    else:
        [breadcrumb] = breadcrumbs
        assert breadcrumb["level"] == severity
        assert breadcrumb["message"] == "first"
        assert breadcrumb["data"] == {
            key: value for key, value in level_fields.items() if key != "level"
        }


@pytest.mark.parametrize("verbose", [False, True])
@pytest.mark.parametrize(
    "level_fields",
    [
        {},
        {"level": "basic_format"},
        {"level": "nonsense"},
        {"level": "nonsense", "sentry": "sent"},
        {"level": None},
        {"level": 40},
        {"level": []},
        {"level_number": "40"},
        {"level_number": 40.0},
    ],
)
def test_unresolved_levels_are_skipped(sentry_events, level_fields, verbose):
    original = {"event": "unresolved", "context": {"value": 1}, **level_fields}
    event_data = original.copy()
    processor = SentryProcessor(level=0, event_level=0, verbose=verbose)

    assert processor(None, "error", event_data) is event_data
    assert event_data == {**original, **({"sentry": "skipped"} if verbose else {})}
    assert not sentry_events

    SentryProcessor()(None, None, {"level": "error", "event": "second"})
    assert not sentry_events[0].get("breadcrumbs", {}).get("values", [])


@pytest.mark.parametrize(
    "numeric_level, severity",
    [
        (-1, "debug"),
        (0, "debug"),
        (19, "debug"),
        (20, "info"),
        (29, "info"),
        (30, "warning"),
        (39, "warning"),
        (40, "error"),
        (49, "error"),
        (50, "fatal"),
        (60, "fatal"),
    ],
)
def test_numeric_severity_for_events_and_breadcrumbs(mocker, numeric_level, severity):
    scope = mocker.Mock(spec=sentry_sdk.Scope)
    scope.capture_event.return_value = None
    event_data = {"event": "custom level", "level_number": numeric_level}
    processor = SentryProcessor(level=-1, event_level=-1, scope=scope, scrub=False)

    assert processor(None, None, event_data) is event_data
    scope.capture_event.assert_called_once()
    scope.add_breadcrumb.assert_called_once()
    assert scope.capture_event.call_args.args[0]["level"] == severity
    assert scope.add_breadcrumb.call_args.args[0]["level"] == severity


def test_structlog_add_log_level_number_without_name(sentry_events):
    log = structlog.wrap_logger(
        structlog.ReturnLogger(),
        processors=[structlog.stdlib.add_log_level_number, SentryProcessor()],
    )

    log.error("numeric level")

    [event] = sentry_events
    assert event["level"] == "error"
    assert event["contexts"]["structlog"] == {
        "event": "numeric level",
        "level_number": logging.ERROR,
    }


@pytest.mark.parametrize("verbose", [False, True])
@pytest.mark.parametrize("failure_source", ["level_lookup", "logger_name", "threshold"])
def test_processor_contains_errors_before_capture(mocker, failure_source, verbose):
    class BrokenLogger:
        @property
        def name(self):
            raise RuntimeError("logger name failed")

    scope = mocker.Mock(spec=sentry_sdk.Scope)
    processor = SentryProcessor(scope=scope, verbose=verbose)
    event_data = {"event": "original", "level": "error"}
    logger = None
    if failure_source == "level_lookup":
        event_data["level"] = "custom"
        mocker.patch("logging.getLevelName", side_effect=RuntimeError("lookup failed"))
    elif failure_source == "logger_name":
        logger = BrokenLogger()
    else:
        processor.event_level = None
    original = event_data.copy()

    assert processor(logger, None, event_data) is event_data
    assert event_data == {**original, **({"sentry": "skipped"} if verbose else {})}
    scope.capture_event.assert_not_called()
    scope.add_breadcrumb.assert_not_called()


def test_processor_contains_sdk_diagnostic_errors(mocker):
    mocker.patch("logging.getLevelName", side_effect=RuntimeError("lookup failed"))
    mocker.patch(
        "sentry_sdk.utils.capture_internal_exception",
        side_effect=RuntimeError("diagnostic failed"),
    )
    event_data = {"event": "original", "level": "custom"}

    assert SentryProcessor()(None, None, event_data) is event_data
    assert event_data == {"event": "original", "level": "custom"}


@pytest.mark.parametrize("level", ["debug", "info", "warning"])
def test_sentry_log_only_errors(sentry_events, level):
    processor_only_errors = SentryProcessor(event_level=logging.ERROR, verbose=True)
    event_dict = processor_only_errors(
        None, None, {"level": level, "event": level + " message"}
    )
    assert not sentry_events
    assert event_dict["sentry"] == "skipped"


@pytest.mark.parametrize("level, severity", [("error", "error"), ("critical", "fatal")])
def test_sentry_log_failure(sentry_events, level, severity):
    """Make sure that events without exc_info=True will have no
    'exception' information after processing
    """
    event_data = {"level": level, "event": level + " message"}
    processor = SentryProcessor(event_level=getattr(logging, level.upper()))
    try:
        1 / 0
    except ZeroDivisionError:
        processor(None, None, event_data)

    assert_event_dict(event_data, sentry_events, sentry_level=severity)
    assert "exception" not in sentry_events[0]
    assert "threads" not in sentry_events[0]


@pytest.mark.parametrize(
    "sentry_events",
    [{"attach_stacktrace": False}, {"attach_stacktrace": True}],
    indirect=True,
)
@pytest.mark.parametrize("stack_info", [False, True])
@pytest.mark.parametrize("level, severity", [("error", "error"), ("critical", "fatal")])
def test_sentry_log_failure_exc_info_true(sentry_events, level, severity, stack_info):
    """Make sure sentry_sdk.utils.exc_info_from_error doesn't raise ValueError
    Because it can't introspect exc_info.
    Bug triggered when logger.error(..., exc_info=True) or logger.exception(...)
    are used.
    """
    event_data = {
        "level": level,
        "event": level + " message",
        "exc_info": True,
        "stack_info": stack_info,
    }
    processor = SentryProcessor(event_level=getattr(logging, level.upper()))
    try:
        1 / 0
    except ZeroDivisionError:
        processor(None, None, event_data)

    assert_event_dict(
        event_data, sentry_events, error=ZeroDivisionError, sentry_level=severity
    )
    [exception] = sentry_events[0]["exception"]["values"]
    assert exception["mechanism"] == {"type": "structlog", "handled": True}
    assert exception["stacktrace"]["frames"][-1]["function"] == (
        "test_sentry_log_failure_exc_info_true"
    )
    assert "threads" not in sentry_events[0]


@pytest.mark.parametrize(
    "sentry_events",
    [{"attach_stacktrace": False}, {"attach_stacktrace": True}],
    indirect=True,
)
@pytest.mark.parametrize(
    "flags",
    [
        {"exc_info": True},
        {"exc_info": (None, None, None)},
        {"stack_info": True},
        {"exc_info": True, "stack_info": True},
        {"exc_info": False, "stack_info": True},
    ],
)
def test_per_call_stack_without_exception(sentry_events, flags):
    log = structlog.wrap_logger(
        structlog.ReturnLogger(),
        processors=[
            structlog.stdlib.add_log_level,
            SentryProcessor(tag_keys="__all__"),
        ],
    )

    _, downstream = log.error("stack requested", **flags)

    [event] = sentry_events
    [thread] = event["threads"]["values"]
    assert set(thread) == {"stacktrace", "crashed", "current"}
    assert thread["crashed"] is False
    assert thread["current"] is True
    assert any(
        frame["function"] == "test_per_call_stack_without_exception"
        for frame in thread["stacktrace"]["frames"]
    )
    assert "exception" not in event
    assert "stacktrace" not in event
    assert event["tags"] == {}
    assert {key: downstream[key] for key in flags} == flags


@pytest.mark.parametrize("flags", [{}, {"exc_info": False, "stack_info": False}])
def test_plain_event_has_no_stack(sentry_events, flags):
    SentryProcessor()(None, "error", {"level": "error", "event": "plain", **flags})

    [event] = sentry_events
    assert "threads" not in event
    assert "stacktrace" not in event
    assert "exception" not in event


@pytest.mark.parametrize(
    "sentry_events, with_locals, with_source",
    [
        ({"include_local_variables": True, "include_source_context": True}, True, True),
        ({"include_local_variables": False}, False, True),
        ({"include_source_context": False}, True, False),
    ],
    indirect=["sentry_events"],
)
@pytest.mark.parametrize("flag", ["exc_info", "stack_info"])
def test_per_call_stack_respects_client_options(
    sentry_events, with_locals, with_source, flag
):
    local_marker = "visible local"
    SentryProcessor()(None, "error", {"level": "error", "event": "stack", flag: True})

    [event] = sentry_events
    frames = event["threads"]["values"][0]["stacktrace"]["frames"]
    [test_frame] = [
        frame
        for frame in frames
        if frame["function"] == "test_per_call_stack_respects_client_options"
    ]
    if with_locals:
        assert local_marker in test_frame["vars"]["local_marker"]
    else:
        assert all("vars" not in frame for frame in frames)
    if with_source:
        assert "SentryProcessor()" in test_frame["context_line"]
    else:
        assert all(
            key not in frame
            for frame in frames
            for key in ("pre_context", "context_line", "post_context")
        )


@pytest.mark.parametrize("sentry_events", [{"max_value_length": 64}], indirect=True)
@pytest.mark.parametrize("flag", ["exc_info", "stack_info"])
def test_per_call_stack_respects_max_value_length(sentry_events, flag):
    SentryProcessor()(None, "error", {"level": "error", "event": "stack", flag: True})

    [event] = sentry_events
    frames = event["threads"]["values"][0]["stacktrace"]["frames"]
    [test_frame] = [
        frame
        for frame in frames
        if frame["function"] == "test_per_call_stack_respects_max_value_length"
    ]
    assert len(test_frame["context_line"]) <= 64
    assert test_frame["context_line"].endswith("...")


@pytest.mark.parametrize(
    "sentry_events", [{"max_value_length": 100_000}], indirect=True
)
@pytest.mark.parametrize("renderer_first", [False, True])
def test_stack_info_renderer_order(sentry_events, renderer_first):
    processors = [
        SentryProcessor(tag_keys="__all__"),
        structlog.processors.StackInfoRenderer(),
    ]
    if renderer_first:
        processors.reverse()
    log = structlog.wrap_logger(
        structlog.ReturnLogger(),
        processors=[structlog.stdlib.add_log_level, *processors],
    )

    _, downstream = log.error("rendered stack", stack_info=True)

    [event] = sentry_events
    assert "test_stack_info_renderer_order" in downstream["stack"]
    assert "stack_info" not in downstream
    assert "exception" not in event
    assert event["tags"] == {}
    if renderer_first:
        assert "threads" not in event
        assert event["contexts"]["structlog"]["stack"] == downstream["stack"]
    else:
        assert event["threads"]["values"][0]["stacktrace"]["frames"]
        assert event["contexts"]["structlog"]["stack_info"] is True


absent = object()


@pytest.mark.parametrize("logger", ["some.logger.name", absent])
def test_sentry_add_logger_name(sentry_events, logger):
    event_data = {"level": "warning", "event": "some.event"}
    if logger is not absent:
        event_data["logger"] = logger

    processor = SentryProcessor(as_context=False)
    processor(None, None, event_data)

    assert_event_dict(event_data, sentry_events)

    if logger is not absent:
        assert event_data["logger"] == sentry_events[0]["logger"]


def test_sentry_log_leave_exc_info_untouched(sentry_events):
    """Make sure exc_info remains in event_data at the end of the processor.

    The structlog built-in format_exc_info processor pops the key and formats
    it. Using SentryProcessor, and format_exc_info wasn't possible before,
    because the latter one didn't have an exc_info to work with.

    https://github.com/kiwicom/structlog-sentry/issues/16
    """
    event_data = {"level": "warning", "event": "some.event", "exc_info": True}
    processor = SentryProcessor(as_context=True)
    try:
        1 / 0
    except ZeroDivisionError:
        processor(None, None, event_data)

    assert "exc_info" in event_data


@pytest.mark.parametrize("level", ["debug", "info", "warning"])
def test_sentry_log_all_as_tags(sentry_events, level):
    event_data = {"level": level, "event": level + " message"}
    processor = SentryProcessor(
        event_level=getattr(logging, level.upper()), tag_keys="__all__"
    )
    processor(None, None, event_data)

    assert_event_dict(event_data, sentry_events)
    assert sentry_events[0]["tags"] == {}
    assert event_data["level"] == sentry_events[0]["contexts"]["structlog"]["level"]
    assert event_data["event"] == sentry_events[0]["contexts"]["structlog"]["event"]


@pytest.mark.parametrize("level", ["debug", "info", "warning"])
def test_sentry_log_specific_keys_as_tags(sentry_events, level):
    event_data = {
        "level": level,
        "event": level + " message",
        "info1": "info1",
        "required": True,
    }
    tag_keys = ["info1", "required", "some non existing key"]
    processor = SentryProcessor(
        event_level=getattr(logging, level.upper()), tag_keys=tag_keys
    )
    processor(None, None, event_data)

    assert_event_dict(event_data, sentry_events)
    assert sentry_events[0]["tags"] == {
        k: str(event_data[k]) for k in tag_keys if k in event_data
    }


def test_sentry_get_logger_name():
    event_data = {
        "level": "info",
        "event": "message",
        "logger": "EventLogger",
        "_record": MockLogger("RecordLogger"),
    }
    assert (
        SentryProcessor._get_logger_name(logger=None, event_dict=event_data)
        == "EventLogger"
    )

    event_data = {
        "level": "info",
        "event": "message",
        "_record": MockLogger("RecordLogger"),
    }
    assert (
        SentryProcessor._get_logger_name(logger=None, event_dict=event_data)
        == "RecordLogger"
    )

    event_data = {
        "level": "info",
        "event": "message",
    }
    assert (
        SentryProcessor._get_logger_name(
            logger=MockLogger("EventLogger"), event_dict=event_data
        )
        == "EventLogger"
    )


@pytest.mark.parametrize(
    "level, severity",
    [
        ("debug", "debug"),
        ("info", "info"),
        ("warning", "warning"),
        ("error", "error"),
        ("critical", "fatal"),
    ],
)
def test_sentry_ignore_logger(sentry_events, level, severity):
    blacklisted_logger = MockLogger("test.blacklisted")
    whitelisted_logger = MockLogger("test.whitelisted")
    processor = SentryProcessor(
        event_level=getattr(logging, level.upper()),
        ignore_loggers=["test.blacklisted"],
        verbose=True,
    )

    event_data = {"level": level, "event": level + " message"}

    blacklisted_logger_event_dict = processor(
        blacklisted_logger, None, event_data.copy()
    )
    whitelisted_logger_event_dict = processor(
        whitelisted_logger, None, event_data.copy()
    )

    assert_event_dict(event_data, sentry_events, sentry_level=severity)
    assert blacklisted_logger_event_dict.get("sentry") == "ignored"
    assert whitelisted_logger_event_dict.get("sentry") != "ignored"


@pytest.mark.parametrize(
    "sentry_events", [{"include_local_variables": False}], indirect=True
)
def test_sentry_json_respects_global_with_locals_option_no_locals(sentry_events):
    processor = SentryProcessor()
    try:
        1 / 0
    except ZeroDivisionError:
        processor(None, None, {"level": "error", "exc_info": True})

    for event in sentry_events:
        for frame in event["exception"]["values"][0]["stacktrace"]["frames"]:
            assert "vars" not in frame  # No local variables were captured


@pytest.mark.parametrize(
    "sentry_events", [{"include_local_variables": True}], indirect=True
)
def test_sentry_json_respects_global_with_locals_option_with_locals(sentry_events):
    processor = SentryProcessor()
    try:
        1 / 0
    except ZeroDivisionError:
        processor(None, None, {"level": "error", "exc_info": True})

    for event in sentry_events:
        for frame in event["exception"]["values"][0]["stacktrace"]["frames"]:
            assert "vars" in frame  # Local variables were captured


base_info_log = {
    "level": "info",
    "event": "Info message",
    "logger": "EventLogger",
    "timestamp": "2024-01-01T00:00:00Z",
}
base_error_log = {
    "level": "error",
    "event": "Error message",
}


def test_breadcrumbs_with_additional_data(sentry_events):
    processor = SentryProcessor(verbose=True)
    processor(None, None, {**base_info_log, **{"foo": "bar"}})
    processor(None, None, base_error_log)
    print({**base_info_log, **{"foo": "bar"}})
    breadcrumbs = sentry_events[0]["breadcrumbs"]["values"]
    del breadcrumbs[0]["timestamp"]
    assert breadcrumbs[0] == {
        "type": "log",
        "level": "info",
        "category": "EventLogger",
        "message": "Info message",
        "data": {"foo": "bar"},
    }


def test_breadcrumbs_with_custom_exclusions(sentry_events):
    processor = SentryProcessor(verbose=True, ignore_breadcrumb_data=["foo"])
    processor(None, None, {**base_info_log, **{"foo": "bar"}})
    processor(None, None, base_error_log)

    breadcrumbs = sentry_events[0]["breadcrumbs"]["values"]

    del breadcrumbs[0]["timestamp"]
    assert breadcrumbs[0] == {
        "type": "log",
        "level": "info",
        "category": "EventLogger",
        "message": "Info message",
        "data": {
            "level": "info",
            "event": "Info message",
            "logger": "EventLogger",
            "timestamp": "2024-01-01T00:00:00Z",
        },
    }


def test_breadcrumbs_with_no_additional_data(sentry_events):
    processor = SentryProcessor(verbose=True)
    processor(None, None, base_info_log)
    processor(None, None, base_error_log)

    breadcrumbs = sentry_events[0]["breadcrumbs"]["values"]

    assert len(breadcrumbs) == 1
    assert isinstance(breadcrumbs[0]["timestamp"], str)
    del breadcrumbs[0]["timestamp"]
    assert breadcrumbs[0] == {
        "type": "log",
        "level": "info",
        "category": "EventLogger",
        "message": "Info message",
        "data": {},
    }


@pytest.mark.parametrize("tag_keys", ["__all__", ["sentry_skip", "request_id"]])
def test_sentry_skip_false_is_not_event_data(sentry_events, tag_keys):
    processor = SentryProcessor(tag_keys=tag_keys)
    processor(
        None,
        None,
        {"level": "error", "event": "x", "sentry_skip": False, "request_id": "own"},
    )

    [event] = sentry_events
    assert "sentry_skip" not in event["tags"]
    assert "sentry_skip" not in event["contexts"]["structlog"]


@pytest.mark.parametrize("tag_keys", ["__all__", ["request_id"]])
def test_tags_and_context_use_supplied_snapshot(sentry_events, tag_keys):
    snapshot = {"level": "error", "event": "x", "request_id": "original"}
    event_data = {**snapshot, "request_id": "changed"}
    processor = SentryProcessor(tag_keys=tag_keys)
    processor._handle_event(event_data, snapshot)

    [event] = sentry_events
    assert event["tags"]["request_id"] == "original"
    assert event["contexts"]["structlog"]["request_id"] == "original"


@pytest.mark.parametrize("tag_keys", ["__all__", ["request_id"]])
def test_downstream_mutation_does_not_change_event_or_breadcrumb(
    sentry_events, tag_keys
):
    def mutate(logger, method_name, event_dict):
        event_dict["request_id"] = "changed"
        return event_dict

    log = structlog.wrap_logger(
        structlog.ReturnLogger(),
        processors=[
            structlog.stdlib.add_log_level,
            SentryProcessor(tag_keys=tag_keys, verbose=True),
            mutate,
        ],
    )
    log.error("first", request_id="original")
    log.error("second")

    first, second = sentry_events
    assert first["tags"]["request_id"] == "original"
    assert first["contexts"]["structlog"]["request_id"] == "original"
    [breadcrumb] = second["breadcrumbs"]["values"]
    assert breadcrumb["data"] == {"request_id": "original"}


@pytest.mark.parametrize(
    "options, event_data",
    [
        ({}, {"level": "info", "event": "breadcrumb only"}),
        ({"active": False}, {"level": "error", "event": "disabled"}),
        ({}, {"level": "error", "event": "skipped", "sentry_skip": True}),
        (
            {"ignore_loggers": ["ignored"]},
            {"level": "error", "event": "ignored", "logger": "ignored"},
        ),
    ],
)
def test_no_event_snapshot_without_capture(sentry_events, mocker, options, event_data):
    copy_dict = mocker.patch("sentry_structlog.dict", wraps=dict, create=True)
    SentryProcessor(**options)(None, None, event_data)

    assert not sentry_events
    copy_dict.assert_not_called()


class TagStatus(Enum):
    READY = "ready"


@pytest.mark.parametrize("tag_keys", ["__all__", ["candidate"]])
@pytest.mark.parametrize(
    "value, expected",
    [
        ("text", "text"),
        ("", ""),
        (42, "42"),
        (1.25, "1.25"),
        (True, "True"),
        (False, "False"),
        (
            UUID("12345678-1234-5678-1234-567812345678"),
            "12345678-1234-5678-1234-567812345678",
        ),
        (Decimal("12.30"), "12.30"),
        (TagStatus.READY, "TagStatus.READY"),
        ({"field": "data"}, None),
        (["data"], None),
        (None, None),
        ("x" * 200, "x" * 200),
        ("x" * 201, None),
        ("x" * 250, None),
        ("first\nsecond", None),
    ],
)
def test_tag_value_policy(sentry_events, tag_keys, value, expected):
    event_data = {"level": "error", "event": "x", "candidate": value}
    SentryProcessor(tag_keys=tag_keys)(None, None, event_data)

    [event] = sentry_events
    assert event["tags"] == ({} if expected is None else {"candidate": expected})
    assert "candidate" in event["contexts"]["structlog"]
    if value is None or isinstance(value, (str, int, float, dict, list)):
        assert event["contexts"]["structlog"]["candidate"] == value


@pytest.mark.parametrize("all_tags", [True, False])
@pytest.mark.parametrize(
    "key, allowed",
    [
        ("valid.A_0:b-c", True),
        ("x" * 32, True),
        ("x" * 33, False),
        ("x" * 40, False),
        ("with space", False),
        ("", False),
        ("line\n", False),
        ("ключ", False),
    ],
)
def test_tag_key_policy(sentry_events, all_tags, key, allowed):
    tag_keys = "__all__" if all_tags else [key]
    SentryProcessor(tag_keys=tag_keys)(
        None, None, {"level": "error", "event": "x", key: "data"}
    )

    [event] = sentry_events
    assert event["tags"] == ({key: "data"} if allowed else {})
    assert event["contexts"]["structlog"][key] == "data"


@pytest.mark.parametrize("keys_type", [list, tuple, set, frozenset, iter])
def test_tag_keys_accept_iterables_for_repeated_events(sentry_events, keys_type):
    processor = SentryProcessor(tag_keys=keys_type(["request_id", "missing"]))
    for request_id in ("first", "second"):
        processor(
            None, None, {"level": "error", "event": "x", "request_id": request_id}
        )

    assert [event["tags"] for event in sentry_events] == [
        {"request_id": "first"},
        {"request_id": "second"},
    ]


@pytest.mark.parametrize("tag_keys", ["request_id", "", "__ALL__"])
def test_tag_keys_reject_other_strings(tag_keys):
    with pytest.raises(ValueError, match="tag_keys"):
        SentryProcessor(tag_keys=tag_keys)


@pytest.mark.parametrize("tag_keys", ["__all__", ("date", "request_id")])
@pytest.mark.parametrize("keys_type", [tuple, set, iter])
def test_exclude_tag_keys(sentry_events, tag_keys, keys_type):
    processor = SentryProcessor(
        tag_keys=tag_keys, exclude_tag_keys=keys_type(["date", "missing"])
    )
    for _ in range(2):
        processor(
            None,
            None,
            {"level": "error", "event": "x", "date": "today", "request_id": "own"},
        )

    assert len(sentry_events) == 2
    for event in sentry_events:
        assert event["tags"] == {"request_id": "own"}
        assert event["contexts"]["structlog"]["date"] == "today"


@pytest.mark.parametrize(
    "reserved_key",
    [
        "event",
        "level",
        "logger",
        "timestamp",
        "exc_info",
        "exception",
        "stack",
        "stack_info",
        "sentry",
        "sentry_id",
        "_record",
        "_from_structlog",
    ],
)
def test_reserved_keys_stay_in_context_only(sentry_events, reserved_key):
    event_data = {reserved_key: "metadata", "level": "error", "event": "x"}
    expected = event_data[reserved_key]
    SentryProcessor(tag_keys="__all__")(None, None, event_data)

    [event] = sentry_events
    assert event["tags"] == {}
    assert event["contexts"]["structlog"][reserved_key] == expected


def test_reserved_key_can_be_selected_explicitly(sentry_events):
    SentryProcessor(tag_keys=("event",))(None, None, {"level": "error", "event": "x"})

    [event] = sentry_events
    assert event["tags"] == {"event": "x"}


@pytest.mark.parametrize(
    "sentry_events",
    [{"event_scrubber": EventScrubber(denylist=["value"], recursive=True)}],
    indirect=True,
)
@pytest.mark.parametrize("options", [{}, {"scrub": True}, {"scrub": False}])
@pytest.mark.parametrize("tag_keys", ["__all__", ("value", "VALUE", "request_id")])
def test_scrub_context_and_drop_sensitive_tags(sentry_events, options, tag_keys):
    event_data = {
        "level": "error",
        "event": "x",
        "value": "secret",
        "VALUE": "upper secret",
        "nested": {"value": "s"},
        "items": [{"value": "list secret"}],
        "request_id": "own",
    }
    SentryProcessor(tag_keys=tag_keys, **options)(None, None, event_data)

    [event] = sentry_events
    context = event["contexts"]["structlog"]
    if options.get("scrub", True):
        assert event["tags"] == {"request_id": "own"}
        assert context["value"] == context["VALUE"] == "[Filtered]"
        assert context["nested"]["value"] == "[Filtered]"
        assert context["items"][0]["value"] == "[Filtered]"
    else:
        assert event["tags"] == {
            "value": "secret",
            "VALUE": "upper secret",
            "request_id": "own",
        }
        assert context["value"] == "secret"
        assert context["VALUE"] == "upper secret"
        assert context["nested"]["value"] == "s"
        assert context["items"][0]["value"] == "list secret"
    assert event_data["value"] == "secret"
    assert event_data["nested"] == {"value": "s"}
    assert event_data["items"] == [{"value": "list secret"}]


@pytest.mark.parametrize("as_context", [True, False])
def test_default_scrubber_filters_password(sentry_events, as_context):
    SentryProcessor(tag_keys="__all__", as_context=as_context)(
        None, None, {"level": "error", "event": "x", "password": "secret"}
    )

    [event] = sentry_events
    assert "password" not in event["tags"]
    if as_context:
        assert event["contexts"]["structlog"]["password"] == "[Filtered]"
    else:
        assert "structlog" not in event.get("contexts", {})


@pytest.mark.parametrize(
    "sentry_events",
    [{"event_scrubber": EventScrubber(denylist=["value"], recursive=False)}],
    indirect=True,
)
def test_scrubbing_respects_nonrecursive_client_setting(sentry_events):
    SentryProcessor(tag_keys="__all__")(
        None,
        None,
        {"level": "error", "event": "x", "value": "secret", "nested": {"value": "s"}},
    )

    [event] = sentry_events
    assert event["tags"] == {}
    assert event["contexts"]["structlog"]["value"] == "[Filtered]"
    assert event["contexts"]["structlog"]["nested"] == {"value": "s"}


def test_capture_without_client_scrubber(sentry_events, monkeypatch):
    client = sentry_sdk.get_client()
    monkeypatch.setitem(client.options, "event_scrubber", None)
    SentryProcessor(tag_keys="__all__")(
        None, None, {"level": "error", "event": "x", "password": "secret"}
    )

    [event] = sentry_events
    assert event["tags"] == {"password": "secret"}
    assert event["contexts"]["structlog"]["password"] == "secret"


def test_build_event_without_scrubber_option(sentry_events, monkeypatch):
    # SDK capture requires the option, but the processor also supports its absence.
    monkeypatch.delitem(sentry_sdk.get_client().options, "event_scrubber")
    event, _ = SentryProcessor(tag_keys="__all__")._get_event_and_hint(
        {"level": "error", "event": "x", "password": "secret"}
    )

    assert event["tags"] == {"password": "secret"}
    assert event["contexts"]["structlog"]["password"] == "secret"


@pytest.mark.parametrize(
    "sentry_events",
    [{"event_scrubber": EventScrubber(denylist=["value"], recursive=True)}],
    indirect=True,
)
def test_recursive_scrubbing_preserves_exception_capture(sentry_events):
    processor = SentryProcessor(tag_keys="__all__")
    try:
        1 / 0
    except ZeroDivisionError:
        processor(
            None,
            None,
            {"level": "error", "event": "x", "exc_info": sys.exc_info(), "value": "s"},
        )

    [event] = sentry_events
    assert event["exception"]["values"][0]["type"] == "ZeroDivisionError"
    assert event["tags"] == {}
    assert event["contexts"]["structlog"]["value"] == "[Filtered]"


@pytest.mark.parametrize(
    "sentry_events",
    [{"event_scrubber": EventScrubber(denylist=["value"], recursive=True)}],
    indirect=True,
)
def test_breadcrumb_scrubbing_is_left_to_sdk(sentry_events):
    processor = SentryProcessor(scrub=False, tag_keys="__all__")
    processor(None, None, {"level": "error", "event": "first", "value": "secret"})
    processor(None, None, {"level": "error", "event": "second"})

    first, second = sentry_events
    assert first["tags"] == {"value": "secret"}
    assert first["contexts"]["structlog"]["value"] == "secret"
    [breadcrumb] = second["breadcrumbs"]["values"]
    assert breadcrumb["data"] == {"value": "[Filtered]"}


class PausingSentryProcessor(SentryProcessor):
    def __init__(self, paused, resume, pause_on, **kwargs):
        super().__init__(**kwargs)
        self.paused = paused
        self.resume = resume
        self.pause_on = pause_on

    def _can_record(self, logger, event_dict):
        if event_dict["event"] == self.pause_on:
            self.paused.set()
            self.resume.wait(5)
        return super()._can_record(logger, event_dict)


def test_tags_and_context_are_not_shared_between_threads(sentry_events):
    paused, resume = threading.Event(), threading.Event()
    processor = PausingSentryProcessor(
        paused,
        resume,
        pause_on="own error",
        event_level=logging.ERROR,
        tag_keys="__all__",
        scope=sentry_sdk.get_isolation_scope(),
    )
    own = {"level": "error", "event": "own error", "request_id": "own"}
    foreign = {"level": "info", "event": "request_finished", "request_id": "foreign"}

    worker = threading.Thread(target=processor, args=(None, None, dict(own)))
    worker.start()
    assert paused.wait(5)
    processor(None, None, dict(foreign))
    resume.set()
    worker.join(5)

    [event] = sentry_events
    assert event["tags"] == {"request_id": "own"}
    assert event["contexts"]["structlog"] == own

# sentry-structlog

| What          | Where                                         |
| ------------- | --------------------------------------------- |
| Documentation | <https://github.com/Barsoomx/sentry-structlog> |
| Maintainer    | @Barsoomx                                       |
| Upstream      | <https://github.com/kiwicom/structlog-sentry>  |

Fork of [kiwicom/structlog-sentry](https://github.com/kiwicom/structlog-sentry) (MIT),
renamed to `sentry-structlog` (import name `sentry_structlog`).

Differences from upstream:

- `SentryProcessor` is thread-safe: tags and the `structlog` context of an event
  are built from the event dict of the current call. Upstream keeps the last
  event dict on the shared processor instance, so under multi-threaded servers
  (granian, gunicorn threads, celery threads) an error could be reported with
  tags and context of an unrelated log line from another thread.

Based on <https://gist.github.com/hynek/a1f3f92d57071ebc5b91>

## Installation

Install the package with [pip](https://pip.pypa.io/):

```
pip install sentry-structlog
```

## Migrating from structlog-sentry

```
pip uninstall structlog-sentry
pip install sentry-structlog
```

and replace `from structlog_sentry import SentryProcessor` with
`from sentry_structlog import SentryProcessor`. Existing constructor arguments keep
their positions. Version 3.0.0 deliberately changes tag and scrubbing defaults; see
[Tags policy](#tags-policy).

## Usage

This module is intended to be used with `structlog` like this:

```python
import sentry_sdk
import structlog
from sentry_structlog import SentryProcessor


sentry_sdk.init()  # pass dsn in argument or via SENTRY_DSN env variable

structlog.configure(
    processors=[
        structlog.stdlib.add_logger_name,  # optional, must be placed before SentryProcessor()
        structlog.stdlib.add_log_level,  # adds the level name before SentryProcessor()
        SentryProcessor(event_level=logging.ERROR),
    ],
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
)


log = structlog.get_logger()
```

Add `structlog.stdlib.add_log_level` (or `structlog.stdlib.add_log_level_number`)
and optionally `structlog.stdlib.add_logger_name` before `SentryProcessor`. The
`SentryProcessor` class takes the following arguments:

- `level` Events of this or higher levels will be reported as Sentry
  breadcrumbs. Default is `logging.INFO`.
- `event_level` Events of this or higher levels will be reported to Sentry
  as events. Default is `logging.WARNING`.
- `active` A flag to make this processor enabled/disabled.
- `as_context` Send `event_dict` as extra info to Sentry. Default is `True`.
- `ignore_breadcrumb_data` A list of data keys that will be excluded from
  [breadcrumb data](https://docs.sentry.io/platforms/python/enriching-events/breadcrumbs/#manual-breadcrumbs).
  Defaults to keys which are already sent separately, i.e. `level`, `logger`,
  `event` and `timestamp`. All other data in `event_dict` will be sent as
  breadcrumb data.
- `tag_keys` Any iterable of keys to send as tags (including lists, tuples, sets,
  and generators), or `"__all__"` for all eligible keys. Defaults to `None` (no
  structlog tags). Any other string raises `ValueError` during construction.
- `exclude_tag_keys` Additional keys to exclude from tags in either mode.
  Defaults to `()`.
- `scrub` Apply the client's event scrubber to `contexts.structlog` and remove
  denylisted tags. Defaults to `True`.
- `ignore_loggers` A list of logger names to ignore any events from.
- `verbose` Report the action taken by the logger in the `event_dict`.
  Default is `False`.
- `scope` Optionally specify `sentry_sdk.Client` (in upstream `structlog-sentry<2.2`
  this corresponds to `hub: sentry_sdk.Hub`).

### Log levels

Levels are resolved in this order:

1. An integer `level_number`, as supplied by `structlog.stdlib.add_log_level_number`.
   This takes precedence over `level` and works without a level name.
2. A case-insensitive name from `structlog.processors.NAME_TO_LEVEL`
   (`_NAME_TO_LEVEL` on older versions), including `exception` as `error` and
   `warn` as `warning`.
3. A name registered with Python logging: first the original spelling, then its
   uppercase spelling. Only integer lookup results are accepted, so mixed-case
   custom names such as `logging.addLevelName(25, "LeVeL")` work.

A non-integer `level_number` falls back to the name. Missing or unrecognized
levels (including `basic_format` and `nonsense`) produce neither an event nor a
breadcrumb. The original event data is retained, with `sentry="skipped"` added in
verbose mode. As with other calls, `sentry_skip` is consumed by the processor.
Errors during processing are contained so application logging can continue.

Both thresholds use the resolved number. Events and breadcrumbs use the same
Sentry severity mapping, including for custom levels:

| Numeric level | Sentry severity |
| --- | --- |
| Below 20 | `debug` |
| 20–29 | `info` |
| 30–39 | `warning` |
| 40–49 | `error` |
| 50 and above | `fatal` |

The original `level` and `level_number` remain unchanged for downstream
processors and in `contexts.structlog` (subject to the configured scrubber).

### Capturing events

Now events are automatically captured by Sentry with `log.error()`:

```python
try:
    1/0
except ZeroDivisionError:
    log.error("zero divsiion")

try:
    resp = requests.get(f"https://api.example.com/users/{user_id}/")
    resp.raise_for_status()
except RequestException:
    log.error("request error", user_id=user_id)
```

This won't automatically collect `sys.exc_info()` along with the message, if you want
to enable this behavior, just pass `exc_info=True`.

When you want to use structlog's built-in
[`format_exc_info`](http://www.structlog.org/en/stable/api.html#structlog.processors.format_exc_info)
processor, make that the `SentryProcessor` comes _before_ `format_exc_info`!
Otherwise, the `SentryProcessor` won't have an `exc_info` to work with, because
it's removed from the event by `format_exc_info`.

Exception events are marked with `mechanism={"type": "structlog", "handled": True}`.
When both `exc_info` and `stack_info` are requested and an exception is available,
the exception's traceback takes priority; no additional thread stack is attached.

To capture the current thread's structured stack without an exception, use:

```python
log.error("current call site", stack_info=True)
log.error("current call site", exc_info=True)  # when no exception is active
```

These calls attach one stack in `threads.values`, with `crashed=False` and
`current=True`, even when the Sentry client's `attach_stacktrace=False`. Per-call
stack capture respects the client's `include_local_variables`,
`include_source_context`, and `max_value_length` options. With
`attach_stacktrace=True`, the SDK client supplies the stack; the processor does
not capture another one. Plain events without either flag only get a stack when
the client's `attach_stacktrace` option is enabled.

Place `SentryProcessor` **before** `structlog.processors.StackInfoRenderer` so it
can read the raw `stack_info` flag, for example:

```python
structlog.configure(processors=[
    structlog.stdlib.add_log_level,
    SentryProcessor(),
    structlog.processors.StackInfoRenderer(),
    structlog.processors.format_exc_info,
    structlog.processors.JSONRenderer(),
])
```

`SentryProcessor` leaves `exc_info`, `stack_info`, and `stack` unchanged for
downstream processors. If `StackInfoRenderer` has already run, it has removed
`stack_info`: the remaining rendered `stack` string is preserved as context
(subject to scrubbing and SDK serialization), but is not converted back into a
structured traceback. Global `attach_stacktrace` still works in that order.
`stack_info` and `stack` remain reserved keys excluded by `tag_keys="__all__"`.

Logging calls with no `sys.exc_info()` are also automatically captured by Sentry
either as breadcrumbs (if configured by the `level` argument) or as events:

```python
log.info("info message", scope="accounts")
log.warning("warning message", scope="invoices")
log.error("error message", scope="products")
```

If you do not want to forward a specific logs into Sentry, you can pass the
`sentry_skip=True` optional argument to logger methods, like this:

```python
log.error("error message", sentry_skip=True)
```

For captured events, tags, `contexts.structlog`, and breadcrumb data use the same
snapshot, taken after removing `sentry_skip` and before adding `sentry_id` or
verbose `sentry` status. Breadcrumb data still respects `ignore_breadcrumb_data`.
Logs below `event_level` do not allocate this event snapshot.

### Sentry Tags

You can set some or all of key/value pairs of structlog `event_dict` as sentry `tags`:

```python
structlog.configure(
    processors=[
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        SentryProcessor(event_level=logging.ERROR, tag_keys=["city", "timezone"]),
    ],...
)

log.error("error message", city="Tehran", timezone="UTC+3:30", movie_title="Some title")
```

this will report the error and the sentry event will have **city** and **timezone** tags.
To select all eligible event data as tags, use `tag_keys="__all__"`.

```python
structlog.configure(
    processors=[
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        SentryProcessor(event_level=logging.ERROR, tag_keys="__all__"),
    ],...
)
```

### Tags policy

In version 3.0.0, `tag_keys="__all__"` always excludes these reserved keys:
`event`, `level`, `logger`, `timestamp`, `exc_info`, `exception`, `stack`,
`stack_info`, `sentry_skip`, `sentry`, `sentry_id`, `_record`, and `_from_structlog`.
An explicit iterable may select reserved keys. `sentry_skip` is consumed before
the snapshot and is never included in tags or `contexts.structlog`.

`exclude_tag_keys` adds consumer exclusions in both selection modes. For example,
exclude high-cardinality or personal fields:

```python
SentryProcessor(
    tag_keys="__all__",
    exclude_tag_keys=("date", "username", "phone", "user_agent", "ip"),
)
```

Tag values of type `str`, `int`, `float`, `bool`, `UUID`, `Decimal`, or `Enum`
are converted with `str()`. `None` and other types (including dictionaries and
lists) are dropped from tags. Values longer than **200 characters** or containing
`\n` are also dropped, rather than truncated. Tag keys must match
`^[a-zA-Z0-9_.:-]{1,32}$`: **1–32 characters**, using only ASCII letters, digits,
underscores, periods, colons, and hyphens.

Excluded keys and rejected values remain in `contexts.structlog` when
`as_context=True`, subject to scrubbing; tag conversion does not stringify the
context values.

`scrub=True` is the default in 3.0.0. If the Sentry client has an `event_scrubber`,
its `scrub_dict()` cleans a separate copy of `contexts.structlog`, respecting the
scrubber's `recursive` setting. Tag keys matching its denylist (case-insensitively)
are removed entirely, rather than assigned `[Filtered]`. For example:

```python
from sentry_sdk.scrubber import EventScrubber

sentry_sdk.init(event_scrubber=EventScrubber(denylist=["value"], recursive=True))
```

With this scrubber, `value="secret"` and `nested={"value": "s"}` are filtered in
the context, and `value` is omitted from tags. `scrub=False` disables these
processor-level protections; it does not disable the tag policy above. If the
client has no event scrubber, the processor leaves context values and eligible
tags unchanged. Breadcrumb data is scrubbed by the SDK, including when
`scrub=False`; the processor does not scrub it a second time.

### Skip Context

By default `SentryProcessor` will send `event_dict` key/value pairs as contextual info to sentry.
Sometimes you may want to skip this, specially when sending the `event_dict` as sentry tags:

```python
structlog.configure(
    processors=[
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        SentryProcessor(event_level=logging.ERROR, as_context=False, tag_keys="__all__"),
    ],...
)
```

### Ignore specific loggers

If you want to ignore specific loggers from being processed by the `SentryProcessor` just pass
a list of loggers when instantiating the processor:

```python
structlog.configure(
    processors=[
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        SentryProcessor(event_level=logging.ERROR, ignore_loggers=["some.logger"]),
    ],...
)
```

### Logging as JSON

If you want to configure `structlog` to format the output as **JSON** (maybe for
[elk-stack](https://www.elastic.co/elk-stack)) you have to disable standard logging
integration in Sentry SDK by passing the `LoggingIntegration(event_level=None, level=None)`
instance to `sentry_sdk.init` method. This prevents duplication of an event reported to sentry:

```python
from sentry_sdk.integrations.logging import LoggingIntegration


INTEGRATIONS = [
    # ... other integrations
    LoggingIntegration(event_level=None, level=None),
]

sentry_sdk.init(integrations=INTEGRATIONS)
```

This integration tells `sentry_sdk` to _ignore_ standard logging and captures the events manually.

## Testing

To run all tests:

```
tox
```

## Contributing

Create a merge request and tag @kiwicom/platform for review.

## Releasing

Set `tool.poetry.version` in `pyproject.toml`, commit the changes, and push to
`master`. The `Publish to PyPI` workflow builds and checks the wheel and source
distribution. Once the checks pass, push the matching version tag, for example:

```sh
git tag v3.0.0
git push origin v3.0.0
```

Tags must match the package version exactly. The tag workflow publishes the
checked artifacts through PyPI Trusted Publishing (OIDC), using
`Barsoomx/sentry-structlog`, `.github/workflows/publish.yml`, and the GitHub
environment `pypi`. No PyPI API token is required. The `pypi` environment allows
deployment only from `v*` tags. Branch pushes, pull requests, and manual workflow
runs check the build without publishing.

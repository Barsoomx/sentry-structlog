# sentry-structlog

[![Tests](https://github.com/Barsoomx/sentry-structlog/actions/workflows/tests.yml/badge.svg)](https://github.com/Barsoomx/sentry-structlog/actions/workflows/tests.yml)
[![PyPI version](https://img.shields.io/pypi/v/sentry-structlog)](https://pypi.org/project/sentry-structlog/)

Send structlog messages to Sentry as Error events and breadcrumbs, with structured
context and optional tags.

Fork of [kiwicom/structlog-sentry](https://github.com/kiwicom/structlog-sentry),
originally authored by Kiwi.com platform and maintained here by
[@Barsoomx](https://github.com/Barsoomx). Distributed under the [MIT license](LICENSE).
Based on [Hynek Schlawack's processor](https://gist.github.com/hynek/a1f3f92d57071ebc5b91).
See the [changelog](CHANGELOG.md) for release history.

This fork builds tags and `contexts.structlog` from a snapshot of each call,
fixing cross-thread substitution of another log line's data on a shared processor.
An explicitly shared Sentry `Scope` is still mutable shared context; see
[Scopes and thread isolation](#scopes-and-thread-isolation).

## Installation and compatibility

```sh
pip install sentry-structlog
```

| Component  | Supported versions / dependency constraint  |
| ---------- | ------------------------------------------- |
| Python     | 3.10–3.14 tested; package requires `>=3.10` |
| sentry-sdk | `>=2.15,<3`                                 |
| structlog  | `>=23.1.0`                                  |

These dependency ranges are broader than the individual versions exercised in CI.
The [CI matrix](#ci) covers the lockfile, minimum and latest stable dependencies;
the SDK 3 prerelease check is advisory and does not expand the supported range.

## Quick start

Replace the example DSN with your project's DSN, then run this complete script:

```python
import logging

import sentry_sdk
import structlog
from sentry_sdk.integrations.logging import LoggingIntegration

from sentry_structlog import SentryProcessor

sentry_sdk.init(
    dsn="https://public@example.com/1",
    disabled_integrations=[LoggingIntegration()],
)
logging.basicConfig(level=logging.INFO, format="%(message)s")
structlog.configure(
    processors=[
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        SentryProcessor(event_level=logging.ERROR),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.JSONRenderer(),
    ],
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
)

log = structlog.get_logger("example")
log.info("job started", job="demo")
try:
    1 / 0
except ZeroDivisionError:
    log.error("job failed", job="demo", exc_info=True)
sentry_sdk.flush()
```

`add_log_level` must precede `SentryProcessor`. Keep the processor before
`format_exc_info` and `StackInfoRenderer`: those processors consume the raw
exception and stack flags. Finish with a renderer for local output.

With an active SDK and no filtering, these two calls capture **one Error event**
and record **two breadcrumbs**, with **no Sentry Logs**. The error event contains
the earlier info breadcrumb; its own breadcrumb is added after capture for later
events. The example overrides the processor's default event threshold of
`WARNING` with `ERROR`.

## Error events, breadcrumbs, and Sentry Logs

`SentryProcessor` captures events at `event_level` and above and records
breadcrumbs at `level` and above. These thresholds are independent. Breadcrumbs
are stored on the scope for subsequent events, not sent as separate Error events.
The processor does not emit native Sentry Logs.

When structlog uses standard logging, the SDK's default `LoggingIntegration` can
capture the same record independently, causing duplicate events or breadcrumbs.
The quick start disables that integration entirely, which works throughout the
supported SDK range and also disables its Sentry Logs handler.

If you keep the integration, `LoggingIntegration(level=None, event_level=None)`
disables only its breadcrumbs and Error events. On SDK versions with Sentry Logs,
`enable_logs=True` (including the older experimental option) or an explicit
`capture_sentry_logs=True` on `LoggingIntegration` can still enable Log items.
A Log item is distinct from an Error event. On versions that accept them,
`capture_sentry_logs=False` or `sentry_logs_level=None` disables that integration's
Logs capture. These keywords, and the top-level `enable_logs` option, are not all
available in SDK 2.15; do not pass them unconditionally when supporting the full
range. See the [SDK logging integration](https://getsentry.github.io/sentry-python/_modules/sentry_sdk/integrations/logging.html).

`sentry_skip=True`, `active=False`, and `ignore_loggers` affect this processor's
events and breadcrumbs only. They do not suppress another integration, an
explicit `sentry_sdk.capture_exception()`, or a later unhandled exception.
Configure those capture paths separately if enabled.

## Migrating from structlog-sentry

```sh
pip uninstall structlog-sentry
pip install sentry-structlog
```

Replace `from structlog_sentry import SentryProcessor` with
`from sentry_structlog import SentryProcessor`. Existing constructor arguments
keep their positions. Review these 3.0.0 behavior changes:

- Python 3.7–3.9 are no longer supported; check the dependency ranges above.
- `scrub=True` is now the default for context and tag protection.
- `tag_keys="__all__"` excludes `RESERVED_TAG_KEYS`; explicit selections can
  include them. Supported scalar tag values become strings; invalid keys,
  oversized or newline-containing values, and unsupported types are dropped.
- `tag_keys` accepts any iterable of keys, but strings other than `"__all__"`
  raise `ValueError`.
- Events and breadcrumbs use Sentry severities, including `fatal` for critical
  levels. Unknown levels are skipped safely.
- `hint["log_record"]` is present only for an actual `logging.LogRecord`;
  structured callback data is in `hint["structlog"]`.
- `scope=` is deprecated and will be removed in 4.0.

See [Tags policy](#tags-policy), [Log levels](#log-levels), and the
[changelog](CHANGELOG.md) for the full contract.

## Processor API

The `SentryProcessor` class takes the following arguments:

- `level` Events of this or higher levels will be reported as Sentry
  breadcrumbs. Default is `logging.INFO`.
- `event_level` Events of this or higher levels will be reported to Sentry
  as events. Default is `logging.WARNING`.
- `active` Enable or disable this processor. Default is `True`.
- `as_context` Send `event_dict` as `contexts.structlog` in Sentry events.
  Default is `True`.
- `ignore_breadcrumb_data` Any iterable of data keys that will be excluded from
  [breadcrumb data](https://docs.sentry.io/platforms/python/enriching-events/breadcrumbs/#manual-breadcrumbs).
  Defaults to keys which are already sent separately, i.e. `level`, `logger`,
  `event` and `timestamp`. All other data in `event_dict` will be sent as
  breadcrumb data, except for the callback-only `_record` object. Copied into a
  `frozenset` at construction, so iterators can be used safely across calls and
  later changes to the source collection have no effect. This option does not
  exclude keys from contexts or tags.
- `tag_keys` Any iterable of keys to send as tags (including lists, tuples, sets,
  and generators), or `"__all__"` for all eligible keys. Defaults to `None` (no
  structlog tags). Any other string raises `ValueError` during construction.
- `exclude_tag_keys` Additional keys to exclude from tags in either mode.
  Defaults to `()`.
- `scrub` Apply the client's event scrubber to `contexts.structlog` and remove
  denylisted tags. Defaults to `True`.
- `ignore_loggers` Any iterable of logger names or wildcard patterns to ignore
  events and breadcrumbs from. Default is `None`. Copied into a `frozenset` at
  construction.
- `verbose` Report the action taken by the logger in the `event_dict`.
  Default is `False`.
- `scope` Deprecated optional `sentry_sdk.Scope`. Passing a scope emits
  `DeprecationWarning`; this parameter will be removed in **4.0**. See
  [Scopes and thread isolation](#scopes-and-thread-isolation).

Add `structlog.stdlib.add_log_level` (or `add_log_level_number`) before the
processor. `add_logger_name` is optional and also belongs before it. Names are
resolved from non-empty strings in `event_dict["logger"]`, `_record.name`, then
the wrapped logger's `name`. If the event dict has no `logger` key, the resolved
name supplies the Sentry event logger and breadcrumb category without changing
original event data. `CapturingLogger` and mock loggers without a string name
are supported.

### Capture status

With `verbose=True`, `sentry="sent"` means the SDK returned an event ID, which is
also added as `sentry_id`. This indicates SDK acceptance, not guaranteed network
delivery. `sentry="dropped"` means the SDK returned no event ID; no `sentry_id` is
added, and the SDK's reason is not inferred. `sentry="ignored"` marks an ignored
logger, while `sentry="skipped"` covers disabled processing, `sentry_skip`, and
level filtering. Breadcrumbs are recorded independently according to `level`,
even when the SDK drops an event. With `verbose=False`, user-supplied `sentry`
metadata is left unchanged.

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
| ------------- | --------------- |
| Below 20      | `debug`         |
| 20–29         | `info`          |
| 30–39         | `warning`       |
| 40–49         | `error`         |
| 50 and above  | `fatal`         |

The original `level` and `level_number` remain unchanged for downstream
processors and in `contexts.structlog` (subject to the configured scrubber).

### Exceptions and per-call stacks

The following calls use `log` from the quick start:

```python
try:
    1 / 0
except ZeroDivisionError:
    log.error("division failed", exc_info=True)

log.error("current call site", stack_info=True)
log.error("current call site", exc_info=True)  # no active exception here
log.error("local output only", sentry_skip=True)
```

A message inside an exception handler does not automatically capture the
exception: pass `exc_info=True`, an exception instance, or an exception tuple.
Exception events use `mechanism={"type": "structlog", "handled": True}`.
When both `exc_info` and `stack_info` are requested and an exception is available,
its traceback takes priority; no additional thread stack is attached.

Without an active exception, `exc_info=True` or `stack_info=True` attaches one
stack in `threads.values`, with `crashed=False` and `current=True`, even when
`attach_stacktrace=False`. Per-call capture respects the client's
`include_local_variables`, `include_source_context`, and `max_value_length`
options. With `attach_stacktrace=True`, the SDK supplies the stack instead.
Plain events without either flag only get a stack when that client option is on.

The processor leaves `exc_info`, `stack_info`, and `stack` unchanged for
subsequent processors. If `StackInfoRenderer` ran first, the rendered `stack`
string remains context, subject to scrubbing and serialization, but cannot be
converted back into a structured traceback. Global `attach_stacktrace` still
works. Both stack keys are reserved for tag selection.

Captured events use one snapshot for tags, `contexts.structlog`, and breadcrumb
data, taken after consuming `sentry_skip` and before adding `sentry_id` or verbose
status. Calls below `event_level` skip the event snapshot; recorded breadcrumbs
still get a separate callback snapshot.

### Callback hints

Both `before_send(event, hint)` and `before_breadcrumb(breadcrumb, hint)` receive
`hint["structlog"]`: a shallow copy of the current call's event dict, taken before
adding `sentry_id` or verbose `sentry` status and after consuming `sentry_skip`.
Changing its top-level keys does not change data passed to downstream processors
or the other callback. Nested values are shared; the hint is not scrubbed.

When the event dict contains a real `logging.LogRecord` under `_record`, such as
with `structlog.stdlib.ProcessorFormatter`, `hint["log_record"]` contains that
record. Otherwise `log_record` is absent; pure structlog logs no longer put a
dict under this key. A callback shared with the SDK's `LoggingIntegration` can
use `hint.get("log_record")` for logger/level filtering and
`hint.get("structlog")` for structured metadata. Place `SentryProcessor` before
`ProcessorFormatter.remove_processors_meta` to retain access to `_record`.

Exception events also retain the SDK's `hint["exc_info"]`. Hints are callback
metadata, not additional event payload fields; a `LogRecord` stored in `_record`
is excluded from `contexts.structlog` and breadcrumb data.

### Selecting tags and context

Replace the processor in the quick start to select tags explicitly:

```python
SentryProcessor(event_level=logging.ERROR, tag_keys=("city", "timezone"))
```

Then `log.error("job failed", city="Prague", timezone="Europe/Prague")` adds those
two tags. Use `tag_keys="__all__"` for all eligible keys, or `as_context=False` to
omit `contexts.structlog`.

| Option                         | Affects                               | Does not remove fields from |
| ------------------------------ | ------------------------------------- | --------------------------- |
| `tag_keys`, `exclude_tag_keys` | Event tags                            | Context and breadcrumb data |
| `as_context=False`             | `contexts.structlog`                  | Tags and breadcrumb data    |
| `ignore_breadcrumb_data`       | Breadcrumb data                       | Tags and context            |
| `scrub=True`                   | Processor context and denylisted tags | Unscrubbed callback hints   |

`ignore_breadcrumb_data` defaults to `level`, `logger`, `event`, and `timestamp`,
which are already separate breadcrumb fields. A real `_record` is also excluded
from context and breadcrumb data. The SDK's event scrubber, when configured,
handles breadcrumb data while preparing the containing event.
Avoid placing secrets in messages or other fields merely because tags exclude
them; this policy is not a general-purpose sanitizer for all channels.

To ignore logger names or wildcard patterns in this processor, replace it with:

```python
SentryProcessor(ignore_loggers=("noisy.logger", "noisy.worker.*"))
```

### Tags policy

`RESERVED_TAG_KEYS` is exported from `sentry_structlog`. In version 3.0.0,
`tag_keys="__all__"` always excludes these reserved keys:
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

The SDK's `EventScrubber.scrub_event()` does not scrub arbitrary `tags` or
`contexts`; this processor adds that protection for its own payload.
`scrub=True` is the default in 3.0.0. If the Sentry client has an `event_scrubber`,
its `scrub_dict()` cleans a separate copy of `contexts.structlog`, respecting the
scrubber's `recursive` setting. Tag keys matching its denylist (case-insensitively)
are removed entirely, rather than assigned `[Filtered]`. For example:

```python
from sentry_sdk.scrubber import EventScrubber

# Replace the sentry_sdk.init() call in the quick start with this one.
sentry_sdk.init(
    dsn="https://public@example.com/1",
    disabled_integrations=[LoggingIntegration()],
    event_scrubber=EventScrubber(denylist=["value"], recursive=True),
)
```

With this scrubber, `value="secret"` and `nested={"value": "s"}` are filtered in
the context, and `value` is omitted from tags. `scrub=False` disables these
processor-level protections; it does not disable the tag policy above. If the
client has no event scrubber, the processor leaves context values and eligible
tags unchanged. When configured, the SDK's event scrubber handles breadcrumb
data while preparing the containing event, even with `scrub=False`; the
processor does not scrub it a second time.

### Scopes and thread isolation

Configure shared processors as `SentryProcessor()` without `scope=`. In
sentry-sdk 2.x, the client attached to a pinned scope is ignored: the SDK selects
the client from its current, isolation, or global scope. The pinned scope still
merges its breadcrumbs, tags, and user into every event captured through it,
including events from other threads.

For a temporary client override, use
`with sentry_sdk.new_scope() as scope:` followed by `scope.set_client(client)`.
For separate threads or requests, enter an isolation scope in each worker and
bind its client there:

```python
# log uses a shared SentryProcessor() configured without scope=.
def handle_request(client):
    with sentry_sdk.isolation_scope() as scope:
        scope.set_client(client)
        log.error("request failed")
```

Record request-specific breadcrumbs, tags, and user inside that isolation scope.
The per-call event snapshot does not transfer request context to another thread:
pass the needed context explicitly and bind it inside the worker's isolation scope.
The processor resolves the active scope on each call. `scope=` remains supported
with a caller-located deprecation warning until its removal in 4.0.

## Output recipes

### JSON and ConsoleRenderer

The quick start emits JSON. For a development console, replace its processor list
with the following, keeping the same SDK and standard logging setup:

```python
structlog.configure(
    processors=[
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        SentryProcessor(event_level=logging.ERROR),
        structlog.processors.StackInfoRenderer(),
        structlog.dev.ConsoleRenderer(),
    ],
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
)
```

`ConsoleRenderer` handles exception formatting itself; omit `format_exc_info`
when using it. Renderer choice does not change the Sentry capture paths.

### Standard logging with ProcessorFormatter

After the imports and SDK initialization from the quick start, use this logging
configuration instead of its `basicConfig` and `structlog.configure` calls. It
processes both structlog and standard logging records through one handler:

```python
shared_processors = [
    structlog.stdlib.add_logger_name,
    structlog.stdlib.add_log_level,
]
formatter = structlog.stdlib.ProcessorFormatter(
    foreign_pre_chain=shared_processors,
    processors=[
        SentryProcessor(event_level=logging.ERROR),
        structlog.stdlib.ProcessorFormatter.remove_processors_meta,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.JSONRenderer(),
    ],
)
handler = logging.StreamHandler()
handler.setFormatter(formatter)
root = logging.getLogger()
root.handlers.clear()
root.addHandler(handler)
root.setLevel(logging.INFO)
structlog.configure(
    processors=[
        *shared_processors,
        structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
    ],
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
)

structlog.get_logger("example").error("structured error", job="demo")
logging.getLogger("example").error("standard logging error")
sentry_sdk.flush()
```

With `LoggingIntegration` disabled as above and no SDK filtering, these calls
capture two Error events and record two breadcrumbs, with no Sentry Logs. Keep
`SentryProcessor` only in the formatter for this recipe; putting another instance
in `shared_processors`, or applying this formatter on multiple handlers, captures
the same record more than once. Placing it before `remove_processors_meta`
preserves `_record` for callback hints.

## Typing

The package ships inline annotations and a PEP 561 `py.typed` marker in both the
wheel and source distribution. CI installs the wheel into a separate environment,
runs [the consumer](scripts/wheel_consumer.py) outside the checkout with Python's
isolated mode, and checks it with strict Mypy targeting Python 3.10. This verifies
that installed consumers can discover and use the shipped types; it does not
promise full strictness for every API or mypyc compatibility.

## Development

Use Python 3.10 or newer and [uv](https://docs.astral.sh/uv/). The artifact checker
uses `tomllib`, so run the full development checklist with Python 3.11 or newer
(CI's quality and packaging jobs use Python 3.12):

```sh
uv sync --locked --all-groups
uv run --no-sync pytest --cov --cov-report=term-missing --cov-report=xml --junitxml=junit.xml
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync mypy sentry_structlog
uv build
uv run --no-sync twine check --strict dist/*
uv run --no-sync python scripts/check_artifacts.py
```

`uv sync` installs the default `dev` group. For a quick test run use
`uv run --no-sync pytest -q`; to format locally use `uv run --no-sync ruff format .`.
When intentionally updating dependencies, run `uv lock` and commit `uv.lock`.
Mypy is the configured type checker; there is no Pyright CI job or project config.

Optional [pre-commit hooks](.pre-commit-config.yaml) run Ruff, Mypy, basic file
checks, and Prettier for Markdown. Pre-commit is not in the dev dependency group:

```sh
uv tool run pre-commit install
uv tool run pre-commit run --all-files
```

### CI

The [Tests workflow](.github/workflows/tests.yml) runs on pushes to `master`, pull
requests, a weekly schedule, and calls from the publish workflow. Its blocking
checks are:

| Job                                | Coverage                                                                                        |
| ---------------------------------- | ----------------------------------------------------------------------------------------------- |
| `Code Quality (Ruff, Mypy)`        | Lint, formatting, package typing on Python 3.12                                                 |
| `Python <version> (<os>)`          | Locked dependencies on Ubuntu with Python 3.10–3.14 and Windows 2022 with Python 3.12           |
| `Dependencies (min)`               | Python 3.10, `sentry-sdk==2.15.0`, `structlog==23.1.0`                                          |
| `Dependencies (latest)`            | Python 3.10, latest stable versions within the declared ranges                                  |
| `Package and typed wheel consumer` | Build, strict Twine check, artifact contents, isolated installed-wheel consumer and strict Mypy |

`Sentry SDK prerelease` additionally tries SDK 3 prereleases on Python 3.12 with
`continue-on-error`; it is advisory. Dependency lanes use `uv run --no-sync`
after installing their selected versions so the lock cannot replace them.

Coverage includes branches and has a 90% gate. `coverage.xml` and `junit.xml` are
uploaded even on failures as `reports-<os>-py<version>` and
`reports-dependencies-<endpoint>` workflow artifacts. There is no external
coverage badge service configured.

### Docker

Build the development image and run the full test suite:

```sh
docker compose build
docker compose run --rm app
docker compose run --rm app uv run --no-sync ruff check .
```

The [Docker setup](docker-compose.yml) uses Python 3.12 and bind-mounts the checkout
at `/app`, so source edits are available immediately. The image keeps its locked
Python environment at `/opt/venv`, separate from any host `.venv`. Rebuild after
changing `pyproject.toml` or `uv.lock`.

## Contributing

Open an [issue](https://github.com/Barsoomx/sentry-structlog/issues) or
[pull request](https://github.com/Barsoomx/sentry-structlog/pulls) in this fork.
The maintainer is [@Barsoomx](https://github.com/Barsoomx). Include a reproduction
for bugs and run the relevant development checks before submitting changes.

## Releasing

Set `project.version` in `pyproject.toml`, update [CHANGELOG.md](CHANGELOG.md),
refresh `uv.lock` as needed, commit, and push to `master`. Wait for the checks on
that commit, then push a matching version tag, for example:

```sh
git tag v3.0.0
git push origin v3.0.0
```

The [Publish to PyPI workflow](.github/workflows/publish.yml) requires the tag to
match `v<project.version>` exactly. `Build and check distributions` validates the
wheel and source distribution; the reusable Tests workflow runs on the same
commit. `Publish with PyPI Trusted Publishing` depends on both build and tests,
including the required jobs above, and publishes the already checked artifacts.
Only `v*` tag pushes in `Barsoomx/sentry-structlog` publish. Branch pushes, pull
requests, and manual runs check the build without publishing.

Configure PyPI Trusted Publishing for owner `Barsoomx`, repository
`sentry-structlog`, workflow filename `publish.yml`, and GitHub environment `pypi`.
Configure that environment to allow deployment only from `v*` tags. The workflow
requests `id-token: write` for OIDC; no PyPI API token is required.

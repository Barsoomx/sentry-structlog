# Django/Granian end-to-end test

On Linux with Python 3.12:

```sh
uv sync --locked --all-groups --python 3.12
uv run --no-sync pytest -m e2e test/e2e -q
```

Or, using the same Docker image as the unit tests:

```sh
docker compose build e2e
docker compose run --rm e2e
```

The default `uv run pytest` excludes this marker and skips collecting the e2e
module. For each processor variant, the explicit command starts one Granian WSGI
worker with eight blocking threads, sends 500 requests from 32 client threads, and
stops the server even on failure. No Sentry server is needed: a synchronous, locked
JSONL transport writes events in pytest's temporary directory alongside
`granian.log`.

Assertions tie each event to Django's recorded request query string and the HTTP
response, checking marker/request ID isolation, recursive phone scrubbing, tag
exclusions, plain-error stack behavior, and request breadcrumbs. The recorded
request is an independent reference: matching tags and context alone would miss
both being replaced by the same foreign request.

The test runs both `fork` and `upstream` variants, selected in the server with
`E2E_PROCESSOR` (default: `fork`). `UpstreamLikeProcessor` reproduces the upstream
2.2.1 shared-snapshot algorithm: every log call assigns
`self._original_event_dict = dict(event_dict)` on the processor instance. A
`time.sleep(0.001)` yield before reading that attribute for `contexts.structlog`
and `"__all__"` tags exposes foreign request markers under concurrent load. The
rest of the 3.0.0 pipeline, including privacy filtering, stays in place.

The fork must pass. The upstream case is a negative canary marked
`xfail(strict=True, raises=AssertionError)`: expected output is **1 passed,
1 xfailed**. An upstream XPASS fails the run because the scenario no longer
detects the race; server startup and other non-assertion errors also fail the run.

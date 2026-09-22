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
module. The explicit command starts one Granian WSGI worker with eight blocking
threads, sends 500 requests from 32 client threads, and stops the server even on
failure. No Sentry server is needed: a synchronous, locked JSONL transport writes
events in pytest's temporary directory alongside `granian.log`.

Assertions tie each event to Django's recorded request query string and the HTTP
response, checking marker/request ID isolation, recursive phone scrubbing, tag
exclusions, plain-error stack behavior, and request breadcrumbs. The recorded
request is an independent reference: matching tags and context alone would miss
both being replaced by the same foreign request.

The upstream 2.2.1 algorithm (git commit `31fa5e3`) assigned
`self._original_event_dict = dict(event_dict)` on every log call and copied that
shared attribute into tags. A mutation check in a disposable `/tmp` copy restored
that assignment and made tag iteration read the shared attribute. A 1 ms yield
before that read exposed the race: the unchanged e2e failed, with 375 foreign tag
markers and 107 missing markers among 500 events. Current privacy filtering was
kept, isolating the concurrency defect. Without the scheduling yield, that run
passed; a finite load test cannot guarantee every possible thread interleaving.

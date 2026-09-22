# Changelog

All notable changes to this project are documented here, following
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

For version 2.2.1 and earlier, see the
[kiwicom/structlog-sentry GitHub Releases](https://github.com/kiwicom/structlog-sentry/releases).

## [Unreleased]

## [3.0.0] - 2026-09-23

### Changed

- Renamed the distribution to `sentry-structlog` and the import module to
  `sentry_structlog`.
- Enabled `scrub=True` by default to scrub `contexts.structlog` and remove
  denylisted tags using the active client's event scrubber.
- Excluded `RESERVED_TAG_KEYS` from `tag_keys="__all__"` by default. Explicit
  selections can still include reserved keys.
- Coerced supported scalar tag values to strings and enforced Sentry tag limits:
  valid keys of 1–32 characters, values up to 200 characters, and no newlines.
  Unsupported or invalid values are dropped.
- Allowed any iterable for `tag_keys`; strings other than `"__all__"` now raise
  `ValueError` at construction.
- Normalised event and breadcrumb levels to Sentry severities, including custom
  numeric levels (`critical` becomes `fatal`).
- Migrated packaging to PEP 621 and uv; refreshed release metadata, attribution,
  and README installation, migration, configuration, and development guidance.

### Fixed

- Prevented cross-thread leaks of tags and `contexts.structlog` by building them
  from a snapshot of the current call, without retaining event data on the shared
  processor.
- Made level resolution safe: missing, malformed, unknown, and custom levels
  never raise into application logging; integer `level_number` takes precedence.
- Resolved logger names from event data, log records, or wrapped loggers without
  treating `CapturingLogger` or `Mock` attributes as valid string names.
- Froze iterable exclusions and logger patterns at construction so generators
  remain effective across calls and later collection mutations do not affect them.
- Reported `sentry="dropped"` in verbose mode when the SDK returns no event ID,
  without adding `sentry_id`; breadcrumbs remain independently eligible.
- Included `hint["log_record"]` only for a real `logging.LogRecord`, and excluded
  that record from contexts and breadcrumb data.

### Added

- `exclude_tag_keys` for additional exclusions in both tag selection modes.
- `scrub` to control processor-level context and tag scrubbing.
- Exported `RESERVED_TAG_KEYS` for consumers of the tag policy.
- Marked captured exceptions with the `structlog` mechanism and `handled=True`.
- Captured per-call stacks for `exc_info=True` without an active exception and
  `stack_info=True`, respecting SDK stack options and avoiding duplicate stacks.
- Supplied independent shallow `hint["structlog"]` snapshots to `before_send` and
  `before_breadcrumb`, retaining the SDK's exception hint when present.
- Packaged inline type annotations and the PEP 561 `py.typed` marker, with an
  installed-wheel consumer check.
- Added a Docker development environment with an isolated container virtualenv.
- Added CI for Python 3.10–3.14 on Linux and Python 3.12 on Windows, minimum/latest
  dependency lanes, a non-blocking Sentry SDK prerelease lane, weekly checks,
  coverage, linting, typing, artifact validation, and Trusted Publishing gates.

### Deprecated

- `scope=` emits `DeprecationWarning` and will be removed in 4.0. Use the SDK's
  current or isolation scope instead of pinning a shared mutable scope.

### Removed

- Support for Python 3.7–3.9.
- Poetry and tox configuration; use uv and the documented development commands.

[Unreleased]: https://github.com/Barsoomx/sentry-structlog/compare/v3.0.0...HEAD
[3.0.0]: https://github.com/Barsoomx/sentry-structlog/releases/tag/v3.0.0

"""Minimal consumer stack, imported only in the Granian worker."""

import json
import logging
import logging.config
import os
import threading
from pathlib import Path

import sentry_sdk
import structlog
from django.conf import settings
from django.core.wsgi import get_wsgi_application
from django.http import JsonResponse
from django.urls import path
from sentry_sdk.integrations.django import DjangoIntegration
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.scrubber import EventScrubber
from sentry_sdk.transport import Transport

from sentry_structlog import SentryProcessor


class JsonlFileTransport(Transport):
    def __init__(self, options):
        super().__init__(options)
        self.path = Path(os.environ["SENTRY_E2E_EVENTS"])
        self.lock = threading.Lock()

    def capture_envelope(self, envelope):
        for item in envelope.items:
            if item.type == "event":
                line = json.dumps(item.get_event()) + "\n"
                with self.lock, self.path.open("a", encoding="utf-8") as stream:
                    stream.write(line)


settings.configure(
    SECRET_KEY="sentry-structlog-e2e-only",
    DEBUG=False,
    ALLOWED_HOSTS=["127.0.0.1", "localhost"],
    ROOT_URLCONF=__name__,
    INSTALLED_APPS=[],
    MIDDLEWARE=["django_structlog.middlewares.RequestMiddleware"],
    LOGGING_CONFIG=None,
)

sentry_sdk.init(
    dsn="http://k@localhost/1",
    transport=JsonlFileTransport,
    send_default_pii=True,
    event_scrubber=EventScrubber(denylist=["phone"], recursive=True),
    integrations=[
        DjangoIntegration(),
        LoggingIntegration(event_level=None, level=None),
    ],
)

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.filter_by_level,
        structlog.processors.TimeStamper(key="date"),
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.StackInfoRenderer(),
        SentryProcessor(
            event_level=logging.ERROR,
            as_context=True,
            tag_keys="__all__",
            exclude_tag_keys=("date",),
        ),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
    ],
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)
logging.config.dictConfig(
    {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "json": {
                "()": structlog.stdlib.ProcessorFormatter,
                "processors": [
                    structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                    structlog.processors.JSONRenderer(),
                ],
            },
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "json",
            },
        },
        "root": {"handlers": ["console"], "level": "INFO"},
    }
)
logger = structlog.get_logger("e2e")


def log_view(request):
    marker = request.GET["request_marker"]
    structlog.contextvars.bind_contextvars(request_marker=marker)
    logger.error(
        "e2e_error",
        phone="+79990000000",
        request_marker=marker,
        nested={"phone": "+79990000000"},
    )
    return JsonResponse(
        {
            "request_marker": marker,
            "request_id": structlog.contextvars.get_contextvars()["request_id"],
        }
    )


urlpatterns = [path("log/", log_view)]
application = get_wsgi_application()

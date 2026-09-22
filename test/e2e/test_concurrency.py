import json
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs
from uuid import uuid4

import pytest
import requests


@pytest.mark.e2e
@pytest.mark.parametrize(
    "e2e_server",
    [
        "fork",
        pytest.param(
            "upstream",
            marks=pytest.mark.xfail(
                strict=True,
                raises=AssertionError,
                reason="upstream shared snapshot leaks foreign markers",
            ),
        ),
    ],
    indirect=True,
)
def test_concurrent_requests_keep_tags_context_and_breadcrumbs_isolated(e2e_server):
    base_url, events_path = e2e_server
    markers = [uuid4().hex for _ in range(500)]

    def send_request(marker):
        response = requests.get(
            f"{base_url}/log/", params={"request_marker": marker}, timeout=30
        )
        response.raise_for_status()
        data = response.json()
        assert data["request_marker"] == marker
        assert isinstance(data["request_id"], str) and data["request_id"]
        return marker, data["request_id"]

    with ThreadPoolExecutor(max_workers=32) as executor:
        request_ids = dict(executor.map(send_request, markers))

    assert len(request_ids) == len(set(request_ids.values())) == 500
    # The transport writes synchronously, before the view returns its response.
    events = [json.loads(line) for line in events_path.read_text().splitlines()]
    assert len(events) == 500
    seen_markers = set()
    for event in events:
        context = event["contexts"]["structlog"]
        tags = event["tags"]
        # DjangoIntegration records the actual request, independently of structlog.
        marker = parse_qs(event["request"]["query_string"])["request_marker"][0]
        assert marker in request_ids
        assert marker not in seen_markers
        seen_markers.add(marker)
        assert tags["request_marker"] == context["request_marker"] == marker
        assert tags["request_id"] == context["request_id"] == request_ids[marker]
        assert "phone" not in tags
        assert context["phone"] == "[Filtered]"
        assert context["nested"]["phone"] == "[Filtered]"
        assert not {"event", "level", "exc_info", "date", "stack"}.intersection(tags)
        assert event["message"] == "e2e_error"
        assert event["level"] == "error"
        assert "exception" not in event
        assert "threads" not in event
        breadcrumbs = event.get("breadcrumbs", {}).get("values", [])
        # RequestMiddleware's request_started must exercise breadcrumb isolation.
        assert any(crumb.get("message") == "request_started" for crumb in breadcrumbs)
        for crumb in breadcrumbs:
            data = crumb.get("data", {})
            if "request_id" in data:
                assert data["request_id"] == request_ids[marker]
    assert seen_markers == set(markers)

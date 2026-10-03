import json

import httpx
import pytest

from rom_newsletter.archive_only import publish_archive_only

KEY = "reviewed:test"
ID = "em_review"


def email(status="draft", mode="enabled", **extra):
    return {
        "id": ID, "status": status, "archival_mode": mode,
        "metadata": {"rom_newsletter_issue": KEY}, **extra,
    }


def test_archive_import_uses_one_patch_and_readback():
    calls = []
    state = email()

    def handle(request):
        calls.append((request.method, request.url.path))
        if request.method == "PATCH":
            payload = json.loads(request.content)
            assert payload == {"status": "imported", "archival_mode": "archive_only"}
            state.update(payload)
        return httpx.Response(200, json=state)

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        assert publish_archive_only(client, ID, KEY)["status"] == "imported"
        publish_archive_only(client, ID, KEY)
    assert [method for method, _ in calls] == ["GET", "PATCH", "GET", "GET"]
    assert all(path == f"/v1/emails/{ID}" for _, path in calls)


@pytest.mark.parametrize("status", ["sent", "about_to_send", "scheduled", "in_flight", "deleted"])
def test_delivery_states_are_rejected_without_writes(status):
    calls = []

    def handle(request):
        calls.append(request.method)
        return httpx.Response(200, json=email(status, "archive_only"))

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(ValueError, match="draft or imported"):
            publish_archive_only(client, ID, KEY)
    assert calls == ["GET"]


def test_archive_identity_mismatch_stops_before_writes():
    with httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=email(id="wrong"))
    )) as client:
        with pytest.raises(ValueError, match="identity"):
            publish_archive_only(client, ID, KEY)


@pytest.mark.parametrize("accepted", [True, False])
def test_ambiguous_patch_is_read_back_without_repeating_write(accepted):
    state = email()
    calls = []

    def handle(request):
        calls.append(request.method)
        if request.method == "PATCH":
            if accepted:
                state.update(status="imported", archival_mode="archive_only")
            raise httpx.ReadTimeout("ambiguous", request=request)
        return httpx.Response(200, json=state)

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        if accepted:
            assert publish_archive_only(client, ID, KEY)["status"] == "imported"
        else:
            with pytest.raises(RuntimeError, match="unconfirmed"):
                publish_archive_only(client, ID, KEY)
    assert calls == ["GET", "PATCH", "GET"]


def test_mismatched_readback_is_not_success():
    calls = []

    def handle(request):
        calls.append(request.method)
        if request.method == "PATCH":
            return httpx.Response(200, json=email("imported", "archive_only"))
        return httpx.Response(200, json=email())

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(RuntimeError, match="unconfirmed"):
            publish_archive_only(client, ID, KEY)
    assert calls == ["GET", "PATCH", "GET"]

import httpx
import pytest

from rom_newsletter.archive_only import publish_archive_only


@pytest.mark.parametrize("mode", ["enabled", "archive_only"])
def test_archive_publication_disabled_without_writes(mode):
    calls = []

    def handle(request):
        calls.append(request.method)
        return httpx.Response(200, json={
            "id": "em_review", "status": "draft", "archival_mode": mode,
            "metadata": {"rom_newsletter_issue": "reviewed:test"},
        })

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(RuntimeError, match="publishing is disabled"):
            publish_archive_only(client, "em_review", "reviewed:test")
    assert calls == ["GET"]


def test_archive_identity_mismatch_stops_before_writes():
    calls = []

    def handle(request):
        calls.append(request.method)
        return httpx.Response(200, json={"id": "wrong"})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(ValueError, match="identity"):
            publish_archive_only(client, "em_review", "reviewed:test")
    assert calls == ["GET"]

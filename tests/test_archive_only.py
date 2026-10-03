import httpx
import pytest

from rom_newsletter.archive_only import publish_archive_only


@pytest.mark.parametrize("confirm_mode", [True, False])
def test_archive_mode_must_be_confirmed_before_publish(confirm_mode):
    import json

    email = {"id": "em_review", "status": "draft", "archival_mode": "enabled",
             "metadata": {"rom_newsletter_issue": "reviewed:test"},
             "body": "DRAFT PREVIEW · NOT SENT"}
    publishes = []

    def handle(request):
        payload = json.loads(request.content) if request.content else {}
        if request.method == "PATCH":
            assert payload["archival_mode"] == "archive_only"
            if confirm_mode:
                email.update(payload)
        if request.method == "POST":
            assert email["archival_mode"] == "archive_only"
            assert payload == {"archival_mode": "archive_only"}
            publishes.append(payload)
            email["status"] = "sent"
        return httpx.Response(200, json=email)

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        if confirm_mode:
            result = publish_archive_only(client, "em_review", "reviewed:test")
            assert result["status"] == "sent"
            assert len(publishes) == 1
            publish_archive_only(client, "em_review", "reviewed:test")
            assert len(publishes) == 1
        else:
            with pytest.raises(ValueError, match="refusing to publish"):
                publish_archive_only(client, "em_review", "reviewed:test")
            assert not publishes


def test_archive_identity_mismatch_stops_before_writes():
    calls = []

    def handle(request):
        calls.append(request.method)
        return httpx.Response(200, json={"id": "wrong"})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(ValueError, match="identity"):
            publish_archive_only(client, "em_review", "reviewed:test")
    assert calls == ["GET"]

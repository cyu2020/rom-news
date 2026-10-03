from pathlib import Path

import httpx
import pytest

from rom_newsletter.buttondown_publish import publish_issue
from rom_newsletter.history import read_json, write_json_atomic

KEY = "rom-sciml-twins:2026-10-04"


class FakeButtondown:
    def __init__(self):
        self.email = None
        self.posts = 0
        self.sends = 0
        self.create_timeout = False
        self.send_timeout = False
        self.accept_create = True
        self.accept_send = True
        self.calls = []

    def handle(self, request):
        import json

        payload = json.loads(request.content) if request.content else {}
        self.calls.append((request.method, payload))
        if request.method == "GET":
            if request.url.path.endswith("/emails"):
                return httpx.Response(200, json={"results": [self.email] if self.email else [], "next": None})
            if self.email is None:
                return httpx.Response(404)
            return httpx.Response(200, json=self.email)
        if request.method == "POST":
            self.posts += 1
            assert payload["status"] == "draft"
            if self.accept_create:
                self.email = {"id": "em_test", **payload}
            if self.create_timeout:
                raise httpx.ReadTimeout("ambiguous create", request=request)
            return httpx.Response(201, json=self.email)
        assert request.method == "PATCH"
        if payload.get("status") == "about_to_send":
            self.sends += 1
            if self.accept_send:
                self.email.update(payload)
            if self.send_timeout:
                raise httpx.ReadTimeout("ambiguous send", request=request)
        else:
            self.email.update(payload)
        return httpx.Response(200, json=self.email)


def publish(tmp_path: Path, fake, **kw):
    return publish_issue(
        html="Week of 2026-10-04",
        subject="New methods",
        issue_key=KEY,
        week_end="2026-10-04",
        token="test-token",
        state_path=tmp_path / "state.json",
        transport=httpx.MockTransport(fake.handle),
        selected_urls=["https://arxiv.org/abs/2610.00002"],
        **kw,
    )


def test_reruns_queue_same_id_once_and_save_sources_before_send(tmp_path):
    fake = FakeButtondown()
    result = publish(tmp_path, fake)
    assert result["status"] == "about_to_send"
    state = read_json(tmp_path / "state.json")["issues"][KEY]
    assert state["email_id"] == "em_test" and state["selected_urls"]
    publish(tmp_path, fake)
    assert fake.posts == 1 and fake.sends == 1
    fake.email["status"] = "sent"
    publish(tmp_path, fake)
    assert fake.posts == 1 and fake.sends == 1


def test_draft_can_be_sent_later(tmp_path):
    fake = FakeButtondown()
    assert publish(tmp_path, fake, draft=True)["status"] == "draft"
    assert fake.sends == 0
    assert publish(tmp_path, fake)["status"] == "about_to_send"
    assert fake.posts == fake.sends == 1


@pytest.mark.parametrize("status", ["sent", "scheduled", "about_to_send", "in_flight"])
def test_draft_only_upload_refuses_existing_published_or_queued_email(tmp_path, status):
    fake = FakeButtondown()
    fake.email = {"id": "em_test", "status": status, "metadata": {"rom_newsletter_issue": KEY}}
    with pytest.raises(RuntimeError, match="Draft-only upload"):
        publish(tmp_path, fake, draft=True)
    assert all(method == "GET" for method, _ in fake.calls)
    assert fake.posts == fake.sends == 0


def test_accepted_create_timeout_is_reconciled_without_repost(tmp_path):
    fake = FakeButtondown()
    fake.create_timeout = True
    assert publish(tmp_path, fake)["status"] == "about_to_send"
    assert fake.posts == fake.sends == 1


def test_unconfirmed_create_blocks_next_run(tmp_path):
    fake = FakeButtondown()
    fake.create_timeout = True
    fake.accept_create = False
    with pytest.raises(RuntimeError, match="create outcome"):
        publish(tmp_path, fake)
    with pytest.raises(RuntimeError, match="Previous create"):
        publish(tmp_path, fake)
    assert fake.posts == 1 and fake.sends == 0
    assert read_json(tmp_path / "state.json")["issues"][KEY]["create_pending"]


def test_accepted_send_timeout_is_reconciled_without_resend(tmp_path):
    fake = FakeButtondown()
    fake.send_timeout = True
    assert publish(tmp_path, fake)["status"] == "about_to_send"
    publish(tmp_path, fake)
    assert fake.sends == 1


def test_unconfirmed_send_blocks_next_run_but_can_recover_if_later_accepted(tmp_path):
    fake = FakeButtondown()
    fake.send_timeout = True
    fake.accept_send = False
    with pytest.raises(RuntimeError, match="send outcome"):
        publish(tmp_path, fake)
    with pytest.raises(RuntimeError, match="Previous send"):
        publish(tmp_path, fake)
    assert fake.sends == 1
    fake.email["status"] = "sent"
    publish(tmp_path, fake)
    assert not read_json(tmp_path / "state.json")["issues"][KEY]["send_pending"]
    assert fake.sends == 1


def test_saved_deleted_id_does_not_create_a_new_email(tmp_path):
    fake = FakeButtondown()
    write_json_atomic(tmp_path / "state.json", {"issues": {KEY: {"email_id": "em_deleted"}}})
    with pytest.raises(httpx.HTTPStatusError):
        publish(tmp_path, fake)
    assert fake.posts == 0


def test_remote_metadata_recovers_id_without_local_state(tmp_path):
    fake = FakeButtondown()
    fake.email = {"id": "em_test", "status": "sent", "metadata": {"rom_newsletter_issue": KEY}}
    assert publish(tmp_path, fake)["id"] == "em_test"
    assert fake.posts == fake.sends == 0


def test_duplicate_remote_issues_fail_closed(tmp_path):
    def handle(request):
        return httpx.Response(
            200,
            json={
                "results": [
                    {"id": "a", "metadata": {"rom_newsletter_issue": KEY}},
                    {"id": "b", "metadata": {"rom_newsletter_issue": KEY}},
                ]
            },
        )

    with pytest.raises(RuntimeError, match="Multiple Buttondown"):
        publish_issue(
            html="html",
            subject="title",
            issue_key=KEY,
            week_end="2026-10-04",
            token="test",
            state_path=tmp_path / "state.json",
            transport=httpx.MockTransport(handle),
        )


def test_publisher_main_updates_history_only_after_acceptance(tmp_path, monkeypatch):
    import hashlib
    from rom_newsletter import buttondown_publish as publisher
    from rom_newsletter.compose import NewsletterDraft
    from rom_newsletter.history import load_seen_urls
    from rom_newsletter.search import SearchHit

    hit = SearchHit(
        "https://arxiv.org/abs/2610.00002", "Neural operator", "Fluid simulation", "arxiv", source_category="papers"
    )
    d = NewsletterDraft(
        subject="New method",
        industry_news={"intro": "No updates", "subsections": []},
        research_papers={
            "intro": "Research",
            "subsections": [{"title": "Operator", "body": "Fluid simulation", "links": [{"url": hit.url}]}],
        },
    )
    stamp = "2026-10-04"
    json_path = tmp_path / f"newsletter-{stamp}.json"
    html_path = tmp_path / f"newsletter-{stamp}.html"
    json_path.write_text(d.model_dump_json())
    html_path.write_text("Week of 2026-10-04")
    history = tmp_path / "published.json"
    write_json_atomic(
        tmp_path / f"newsletter-{stamp}-selection.json",
        {
            "issue_key": KEY,
            "week_end": stamp,
            "selected_urls": [hit.url],
            "history_file": str(history),
            "html_sha256": hashlib.sha256(html_path.read_bytes()).hexdigest(),
            "json_sha256": hashlib.sha256(json_path.read_bytes()).hexdigest(),
        },
    )
    write_json_atomic(tmp_path / f"newsletter-{stamp}-search.json", {"composition_inputs": [vars(hit)]})
    fake = FakeButtondown()
    real_publish = publisher.publish_issue

    def mocked_publish(**kwargs):
        return real_publish(**kwargs, transport=httpx.MockTransport(fake.handle))

    monkeypatch.setattr(publisher, "publish_issue", mocked_publish)
    monkeypatch.setattr(publisher, "get_buttondown_api_key", lambda: "test")
    args = ["--date", stamp, "--output-dir", str(tmp_path), "--state-file", str(tmp_path / "state.json")]
    publisher.main(args + ["--dry-run"])
    assert fake.posts == 0
    publisher.main(args + ["--draft"])
    assert not history.exists()
    publisher.main(args)
    assert load_seen_urls(history) == {hit.url}
    # Regenerating while queued must not mark new, unsent stories as published.
    new_url = "https://arxiv.org/abs/2610.00003"
    d.research_papers.subsections[0].links[0].url = new_url
    json_path.write_text(d.model_dump_json())
    manifest = read_json(tmp_path / f"newsletter-{stamp}-selection.json")
    manifest.update({"selected_urls": [new_url], "json_sha256": hashlib.sha256(json_path.read_bytes()).hexdigest()})
    write_json_atomic(tmp_path / f"newsletter-{stamp}-selection.json", manifest)
    hit.url = new_url
    write_json_atomic(tmp_path / f"newsletter-{stamp}-search.json", {"composition_inputs": [vars(hit)]})
    publisher.main(args)
    assert new_url not in load_seen_urls(history)
    assert fake.posts == fake.sends == 1


def test_remote_queued_email_recovers_its_source_list(tmp_path):
    fake = FakeButtondown()
    fake.email = {
        "id": "em_test",
        "status": "about_to_send",
        "metadata": {"rom_newsletter_issue": KEY, "rom_newsletter_sources": ["https://arxiv.org/abs/2609.00001"]},
    }
    result = publish(tmp_path, fake)
    assert not result["content_updated"]
    assert read_json(tmp_path / "state.json")["issues"][KEY]["selected_urls"] == ["https://arxiv.org/abs/2609.00001"]
    assert fake.posts == fake.sends == 0

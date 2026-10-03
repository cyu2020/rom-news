import pytest


@pytest.fixture(autouse=True)
def isolated_proxy_environment(monkeypatch):
    # All HTTP is mocked. Host proxy configuration should not affect offline tests.
    for name in ("ALL_PROXY", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "http_proxy", "https_proxy"):
        monkeypatch.delenv(name, raising=False)

from fastapi.testclient import TestClient

from sdr_agent.toolkit.web import extract


def test_extract_text_and_same_site_links():
    page = """<html><head><title>예시 &amp; 회사</title><style>.x{}</style></head>
    <body><script>var a=1</script><h1>회계 SaaS</h1>
    <a href="/about">회사 소개</a><a href="https://other.com/x">외부</a><a href="#top">맨위</a></body></html>"""
    r = extract(page, "https://a.kr/")
    assert r["title"] == "예시 & 회사"
    assert "회계 SaaS" in r["text"] and "var a" not in r["text"]
    assert r["links"] == [{"url": "https://a.kr/about", "text": "회사 소개"}]


def test_jobs_require_token(monkeypatch):
    monkeypatch.setenv("JOB_TOKEN", "secret")
    from sdr_agent.app import app

    client = TestClient(app)
    assert client.get("/healthz").json() == {"ok": True}
    assert client.post("/jobs/research", json={}).status_code == 401
    assert client.post("/jobs/research", json={}, headers={"Authorization": "Bearer wrong"}).status_code == 401

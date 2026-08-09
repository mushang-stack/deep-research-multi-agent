from tools import web_read


def test_fetch_text_happy(monkeypatch):
    monkeypatch.setattr(web_read.trafilatura, "fetch_url", lambda url: "<html>x</html>")
    monkeypatch.setattr(web_read.trafilatura, "extract",
                        lambda html, with_metadata=False: "正文" * 10)
    out = web_read.fetch_text("https://example.com/a", max_chars=20)
    assert out.startswith("正文")
    assert len(out) <= 20


def test_fetch_text_empty_when_download_fails(monkeypatch):
    monkeypatch.setattr(web_read.trafilatura, "fetch_url", lambda url: None)
    assert web_read.fetch_text("https://example.com/missing") == ""


def test_fetch_text_empty_when_extract_none(monkeypatch):
    monkeypatch.setattr(web_read.trafilatura, "fetch_url", lambda url: "<html></html>")
    monkeypatch.setattr(web_read.trafilatura, "extract",
                        lambda html, with_metadata=False: None)
    assert web_read.fetch_text("https://example.com/x") == ""


def test_fetch_text_no_truncate_when_zero(monkeypatch):
    monkeypatch.setattr(web_read.trafilatura, "fetch_url", lambda url: "h")
    monkeypatch.setattr(web_read.trafilatura, "extract",
                        lambda html, with_metadata=False: "abcdefgh")
    assert web_read.fetch_text("u", max_chars=0) == "abcdefgh"

"""钉住 recommend.generate()：成功入库、条数不够/回复不是 JSON/模型出错时批次标 failed（模型与核 URL 全用桩）。"""
from __future__ import annotations

import json

import pytest

import app
import recommend


def item(n: int, **kw) -> dict:
    it = dict(category="时令蔬果", name=f"商品{n}", detail="1kg", origin="長野県", price="¥1,000",
              where_buy="店", reason="理由", why_now="旬", caveat="注意", url=f"https://example.jp/{n}",
              how_to_buy="通販", buy_url=f"https://example.jp/buy/{n}", landed_cost="1,200円")
    it.update(kw)
    return it


def stub(monkeypatch, reply: str | Exception, url_ok=lambda url: 1) -> dict:
    seen = {"calls": [], "urls": [], "checked": []}

    def ask(system, prompt, caller):
        seen["calls"].append((system, prompt, caller))
        if isinstance(reply, Exception):
            raise reply
        return reply, "claude-test"

    def check(url):
        seen["urls"].append(url)
        return url_ok(url)

    monkeypatch.setattr(recommend, "ask_claude", ask)
    monkeypatch.setattr(recommend, "url_ok", check)
    monkeypatch.setattr(recommend, "price_check", lambda bid: seen["checked"].append(bid))
    return seen


def batches() -> list[dict]:
    with recommend._db() as c:
        return [dict(r) for r in c.execute("SELECT * FROM rec_batches ORDER BY id")]


def recs() -> list[dict]:
    with recommend._db() as c:
        return [dict(r) for r in c.execute("SELECT * FROM recs ORDER BY pos")]


# ---------- 成功 ----------

def test_generate_stores_items_and_marks_batch_done(monkeypatch):
    seen = stub(monkeypatch, "好的：\n" + json.dumps({"items": [item(i) for i in range(3)], "note": "思路"},
                                                    ensure_ascii=False))
    bid = recommend.generate()
    assert [b["status"] for b in batches()] == ["done"]
    b = batches()[0]
    assert b["id"] == bid and b["model"] == "claude-test" and b["note"] == "思路" and b["error"] is None
    rows = recs()
    assert [(r["batch_id"], r["pos"], r["name"]) for r in rows] == [(bid, 0, "商品0"), (bid, 1, "商品1"), (bid, 2, "商品2")]
    assert rows[0]["url_ok"] == 1 and rows[0]["buy_url_ok"] == 1
    assert json.loads(rows[0]["payload"]) == item(0)
    assert seen["checked"] == [bid]                     # 入库后接着核价


def test_generate_sends_system_prompt_and_context(monkeypatch):
    seen = stub(monkeypatch, json.dumps({"items": [item(i) for i in range(3)]}))
    recommend.generate()
    [(system, prompt, caller)] = seen["calls"]
    assert system == recommend.SYSTEM and caller == "shopping.recommend"
    assert prompt.startswith("## 今天\n")


def test_generate_without_buy_url_leaves_buy_url_ok_null(monkeypatch):
    seen = stub(monkeypatch, json.dumps({"items": [item(0, buy_url=""), item(1, buy_url=None), item(2)]}))
    recommend.generate()
    assert [r["buy_url_ok"] for r in recs()] == [None, None, 1]
    assert seen["urls"] == ["https://example.jp/0", "https://example.jp/1", "https://example.jp/2",
                            "https://example.jp/buy/2"]          # 没有 buy_url 就不去核


def test_generate_records_unreachable_urls(monkeypatch):
    stub(monkeypatch, json.dumps({"items": [item(i) for i in range(3)]}), url_ok=lambda url: 0)
    recommend.generate()
    assert {(r["url_ok"], r["buy_url_ok"]) for r in recs()} == {(0, 0)}


def test_generate_keeps_at_most_ten_items(monkeypatch):
    stub(monkeypatch, json.dumps({"items": [item(i) for i in range(12)]}))
    recommend.generate()
    assert [r["pos"] for r in recs()] == list(range(10))


def test_generate_batch_note_may_be_missing(monkeypatch):
    stub(monkeypatch, json.dumps({"items": [item(i) for i in range(3)]}))
    recommend.generate()
    assert batches()[0]["note"] is None


# ---------- 失败：批次标 failed、不核价、异常往上抛 ----------

@pytest.mark.parametrize("n", [0, 2])
def test_generate_fails_with_fewer_than_three_items(monkeypatch, n):
    seen = stub(monkeypatch, json.dumps({"items": [item(i) for i in range(n)]}))
    with pytest.raises(ValueError, match=f"only {n} items"):
        recommend.generate()
    [b] = batches()
    assert b["status"] == "failed" and b["error"] == f"only {n} items"
    assert recs() == [] and seen["checked"] == []


def test_generate_fails_when_reply_has_no_json(monkeypatch):
    seen = stub(monkeypatch, "抱歉，没查到")
    with pytest.raises(AttributeError):
        recommend.generate()
    assert batches()[0]["status"] == "failed" and seen["checked"] == []


def test_generate_fails_when_items_key_missing(monkeypatch):
    stub(monkeypatch, json.dumps({"note": "x"}))
    with pytest.raises(ValueError, match="only 0 items"):
        recommend.generate()
    assert batches()[0]["status"] == "failed"


def test_generate_fails_when_model_call_fails(monkeypatch):
    seen = stub(monkeypatch, RuntimeError("claude exit 1: " + "e" * 600))
    with pytest.raises(RuntimeError):
        recommend.generate()
    [b] = batches()
    assert b["status"] == "failed" and len(b["error"]) == 500 and b["error"].startswith("claude exit 1")
    assert seen["checked"] == []


def test_failed_batch_is_invisible_to_the_app(monkeypatch):
    stub(monkeypatch, "no json")
    with pytest.raises(AttributeError):
        recommend.generate()
    assert app.recs_state()["batch"] is None

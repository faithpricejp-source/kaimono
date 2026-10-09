"""钉住 recommend.py 里不联网的纯函数：数字解析、核价状态判定、账本摘要、上下文拼装。"""
from __future__ import annotations

import csv
import re
import datetime as dt
import json

import pytest

import app
import recommend

REC = dict(name="納豆", detail="100g", origin="茨城", buy_url="https://x.jp/a",
           price="¥1,980", landed_cost="2,000円")

STATUS_MSG = {"verified": "现价和运费都从页面读到", "partial": "只读到其中一项"}


def answer(d: dict, prose: str = "") -> str:
    return prose + json.dumps(d, ensure_ascii=False)


def stub(monkeypatch, reply: str, url_ok: int = 1, calls: list | None = None):
    def ask(system, prompt, caller):
        if calls is not None:
            calls.append((system, prompt, caller))
        return reply, "claude-test"

    monkeypatch.setattr(recommend, "ask_claude", ask)
    monkeypatch.setattr(recommend, "url_ok", lambda url: url_ok)


# ---------- _int ----------

@pytest.mark.parametrize("raw,want", [("1,980", 1980), ("¥2,000", 2000), ("3 円", 3), (" 500 ", 500),
                                      ("", None), (None, None), ("abc", None), ("1.5", None),
                                      (0, 0), (0.0, None), ("０", 0)])
def test_int_parses_price_strings(raw, want):
    assert recommend._int(raw) == want


# ---------- check_one：核价状态判定 ----------

def test_verified_when_price_and_shipping_read(monkeypatch):
    stub(monkeypatch, answer({"price_jpy": 1980, "shipping_jpy": 350, "source_url": "https://x.jp/a",
                              "status": "verified", "note": "3,980円以上包邮"}))
    r = recommend.check_one(REC)
    assert r["chk_status"] == "verified"
    assert (r["chk_price"], r["chk_shipping"], r["chk_total"]) == (1980, 350, 2330)
    assert r["chk_url"] == "https://x.jp/a" and r["chk_url_ok"] == 1
    assert r["chk_note"] == "3,980円以上包邮"
    assert json.loads(r["chk_payload"])["price_jpy"] == 1980


def test_partial_when_only_price_read(monkeypatch):
    stub(monkeypatch, answer({"price_jpy": 1980, "shipping_jpy": None, "source_url": ""}))
    r = recommend.check_one(REC)
    assert r["chk_status"] == "partial"
    assert (r["chk_price"], r["chk_shipping"], r["chk_total"]) == (1980, None, None)
    assert r["chk_url"] == "" and r["chk_url_ok"] == 0


def test_partial_when_only_shipping_read(monkeypatch):
    stub(monkeypatch, answer({"price_jpy": None, "shipping_jpy": 350}))
    r = recommend.check_one(REC)
    assert r["chk_status"] == "partial"
    assert (r["chk_price"], r["chk_shipping"], r["chk_total"]) == (None, 350, None)


def test_unverified_when_nothing_read(monkeypatch):
    stub(monkeypatch, answer({"price_jpy": None, "shipping_jpy": None}))
    r = recommend.check_one(REC)
    assert r["chk_status"] == "unverified"
    assert (r["chk_price"], r["chk_shipping"], r["chk_total"]) == (None, None, None)


def test_model_reported_status_is_ignored(monkeypatch):
    # 模型自报 verified，但没读到数字 → 仍算 unverified
    stub(monkeypatch, answer({"price_jpy": None, "shipping_jpy": None, "status": "verified"}))
    assert recommend.check_one(REC)["chk_status"] == "unverified"


def test_unreachable_source_downgrades_verified_to_partial(monkeypatch):
    stub(monkeypatch, answer({"price_jpy": 1980, "shipping_jpy": 350, "source_url": "https://x.jp/a"}),
         url_ok=0)
    r = recommend.check_one(REC)
    assert r["chk_status"] == "partial"
    assert r["chk_total"] == 2330 and r["chk_url_ok"] == 0


def test_unreachable_source_keeps_partial(monkeypatch):
    stub(monkeypatch, answer({"price_jpy": 1980, "shipping_jpy": None, "source_url": "https://x.jp/a"}),
         url_ok=0)
    assert recommend.check_one(REC)["chk_status"] == "partial"


def test_free_shipping_counts_as_zero(monkeypatch):
    stub(monkeypatch, answer({"price_jpy": "1,980", "shipping_jpy": "0", "source_url": ""}))
    r = recommend.check_one(REC)
    assert (r["chk_price"], r["chk_shipping"], r["chk_total"]) == (1980, 0, 1980)


def test_note_is_truncated_to_500_chars(monkeypatch):
    stub(monkeypatch, answer({"price_jpy": 1, "shipping_jpy": 1, "note": "x" * 900}))
    assert len(recommend.check_one(REC)["chk_note"]) == 500


def test_json_is_pulled_out_of_surrounding_prose(monkeypatch):
    stub(monkeypatch, answer({"price_jpy": 500, "shipping_jpy": 0, "source_url": ""},
                             prose="我查到了：\n"))
    assert recommend.check_one(REC)["chk_price"] == 500


def test_reply_without_json_raises(monkeypatch):
    stub(monkeypatch, "抱歉，没查到")
    with pytest.raises(AttributeError):
        recommend.check_one(REC)


def test_prompt_carries_the_item_fields(monkeypatch):
    calls = []
    stub(monkeypatch, answer({"price_jpy": None, "shipping_jpy": None}), calls=calls)
    recommend.check_one(REC)
    system, prompt, caller = calls[0]
    assert caller == "shopping.pricecheck"
    assert "核价员" in system
    for s in ("商品：納豆", "规格：100g", "产地/厂家：茨城", "购买页：https://x.jp/a", "¥1,980", "2,000円"):
        assert s in prompt


# ---------- ledger_summary ----------

def write_ledger(tmp_path, rows, fieldnames=("date", "item", "store")) -> None:
    p = tmp_path / "ledger.csv"
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(fieldnames))
        w.writeheader()
        for r in rows:
            w.writerow(r)
    recommend.LEDGER = p


def test_no_ledger_file(monkeypatch, tmp_path):
    monkeypatch.setattr(recommend, "LEDGER", tmp_path / "nope.csv")
    assert recommend.ledger_summary() == "（没有消费账本）"


def test_ledger_counts_items_and_stores(monkeypatch, tmp_path):
    today = dt.date.today()
    write_ledger(tmp_path, [
        {"date": (today - dt.timedelta(days=2)).isoformat(), "item": "牛乳", "store": "まいばすけっと"},
        {"date": (today - dt.timedelta(days=3)).isoformat(), "item": "牛乳", "store": "まいばすけっと"},
        {"date": (today - dt.timedelta(days=4)).isoformat(), "item": "納豆", "store": "ヨドバシ"},
    ])
    s = recommend.ledger_summary()
    assert "牛乳×2" in s and "納豆×1" in s
    assert "まいばすけっと×2" in s and "ヨドバシ×1" in s


def test_ledger_drops_rows_older_than_the_window(monkeypatch, tmp_path):
    today = dt.date.today()
    write_ledger(tmp_path, [
        {"date": (today - dt.timedelta(days=400)).isoformat(), "item": "古い", "store": "old"},
        {"date": (today - dt.timedelta(days=1)).isoformat(), "item": "新しい", "store": "new"},
    ])
    s = recommend.ledger_summary(months=12)
    assert "古い" not in s and "新しい×1" in s


@pytest.mark.parametrize("item", ["Amazon.co.jp 订单 ほげ", "小计 ほげ", "合計 ほげ", "値引 ほげ",
                                  "利息 ほげ", "本金 ほげ"])
def test_ledger_skips_noise_items(monkeypatch, tmp_path, item):
    write_ledger(tmp_path, [{"date": dt.date.today().isoformat(), "item": item, "store": "S"}])
    s = recommend.ledger_summary()
    assert item.split()[0] not in s


def test_ledger_keeps_only_shopping_categories(monkeypatch, tmp_path):
    d = dt.date.today().isoformat()
    write_ledger(tmp_path, [
        {"date": d, "item": "超市·卵", "store": "A"},
        {"date": d, "item": "便利店·おにぎり", "store": "B"},
        {"date": d, "item": "餐饮·ラーメン", "store": "C"},
        {"date": d, "item": "住宅ローン·利息", "store": "D"},
        {"date": d, "item": "通信·スマホ代", "store": "E"},
    ])
    s = recommend.ledger_summary()
    assert "超市·卵×1" in s and "便利店·おにぎり×1" in s and "餐饮·ラーメン×1" in s
    assert "住宅ローン" not in s and "通信" not in s


def test_ledger_category_filter_uses_halfwidth_dot_only(monkeypatch, tmp_path):
    # 同样的条目换成全角「・」，就不再被当财务类过滤掉（现状如此）
    write_ledger(tmp_path, [{"date": dt.date.today().isoformat(), "item": "住宅ローン・利息", "store": "D"}])
    assert "住宅ローン・利息×1" in recommend.ledger_summary()


def test_ledger_item_names_are_capped_at_40_chars(monkeypatch, tmp_path):
    write_ledger(tmp_path, [{"date": dt.date.today().isoformat(), "item": "あ" * 60, "store": "S"}])
    assert "あ" * 40 + "×1" in recommend.ledger_summary()


# ---------- build_context ----------

def seed_recs(feedback, comments=()):
    conn = recommend._db()
    with conn:
        b = conn.execute("INSERT INTO rec_batches(status) VALUES('done')").lastrowid
        ids = {}
        for pos, name in enumerate(("梨", "味噌")):
            ids[name] = conn.execute(
                "INSERT INTO recs(batch_id,pos,category,name,reason) VALUES(?,?,?,?,?)",
                (b, pos, "カテゴリ", name, f"{name}の理由")).lastrowid
        for name, v, cm in feedback:
            app.log_event(conn, "rec_feedback", ids[name], {"verdict": v, "comment": cm})
        for text in comments:
            app.log_event(conn, "rec_comment", None, {"text": text})
    conn.close()


def test_context_seeds_profile_when_missing():
    ctx = recommend.build_context()
    assert recommend.PROFILE.exists()
    assert "偏好档案" in ctx and recommend.PROFILE_SEED.strip() in ctx
    assert "（还没有）" in ctx          # 没有备忘、没有反馈时都写「（还没有）」


def test_context_reads_existing_memo():
    recommend.MEMO.write_text("MEMO-BODY")
    assert "MEMO-BODY" in recommend.build_context()


def test_context_dedups_feedback_by_rec_name_keeping_the_last():
    seed_recs([("梨", "no", None), ("梨", "love", "好き"), ("味噌", "want", None)])
    ctx = recommend.build_context()
    assert ctx.count("- 梨[カテゴリ]") == 1
    assert "- 梨[カテゴリ] → 很对胃口；他说：好き" in ctx
    assert "不感兴趣" not in ctx
    assert "- 味噌[カテゴリ] → 想买（没写理由）" in ctx


def test_context_lists_past_recs_newest_first():
    seed_recs([])
    ctx = recommend.build_context()
    assert "味噌、梨" in ctx


def test_context_shows_batch_comments():
    seed_recs([], ["全体的に高い"])
    ctx = recommend.build_context()
    # 评论日期取自 UTC 时间戳前 10 位，JST 凌晨与本地日期差一天，只钉格式不钉具体日期
    assert re.search(r"\d{4}-\d{2}-\d{2} 全体的に高い", ctx)


def test_context_shows_in_transit_orders_only():
    app.save_order({"item": "納豆", "merchant": "楽天", "status": "shipped"})
    app.save_order({"item": "牛乳", "merchant": "Amazon", "status": "ordered"})
    app.save_order({"item": "米", "merchant": "楽天", "status": "delivered"})
    app.save_order({"item": "卵", "merchant": "楽天", "status": "cancelled"})
    ctx = recommend.build_context()
    assert "- 納豆（楽天）" in ctx and "- 牛乳（Amazon）" in ctx
    assert "米（楽天）" not in ctx and "卵（楽天）" not in ctx


def test_context_heads_with_today_and_ledger(monkeypatch, tmp_path):
    monkeypatch.setattr(recommend, "LEDGER", tmp_path / "nope.csv")
    ctx = recommend.build_context()
    assert ctx.startswith("## 今天\n" + dt.date.today().isoformat() + "，东京。")
    assert "（没有消费账本）" in ctx

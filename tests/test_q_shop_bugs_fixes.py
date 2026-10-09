"""TASK n1007-q-shop-bugs 的修复回归（S4 拒收，测试已剔除）：每条直接调用真函数，钉修复后的行为。

S1 app.save_order  形状对但不存在的 delivery_date（2026-13-45）要拒
S2 app.save_order  id=0 要走更新分支，不再当 falsy 新建
S3 app.save_order  更新不存在的 id 要报错，不再静默成功
S5 remind.due      window_start 解析不了要抛 ValueError，与 save_order 一致
"""
from __future__ import annotations

import datetime as dt

import pytest

import app
import remind

DD = "2026-10-08"


def row(oid) -> dict:
    with app.db() as c:
        r = c.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
    return dict(r) if r else {}


def all_rows() -> list[dict]:
    with app.db() as c:
        return [dict(r) for r in c.execute("SELECT * FROM orders")]


def order(**kw) -> dict:
    o = dict(id=1, merchant="楽天", item="納豆", delivery_date="2026-10-06", window_start=None,
             window_end=None, status="ordered")
    o.update(kw)
    return o


def at(h: int, m: int = 0) -> dt.datetime:
    return dt.datetime(2026, 10, 6, h, m)


# ---------- S1 ----------

def test_s1_impossible_delivery_date_rejected():
    with pytest.raises(ValueError, match="delivery_date 要写成 YYYY-MM-DD"):
        app.save_order({"delivery_date": "2026-13-45"})
    assert all_rows() == []


def test_s1_impossible_delivery_date_rejected_on_update():
    oid = app.save_order({"item": "納豆", "delivery_date": DD})
    with pytest.raises(ValueError, match="delivery_date 要写成 YYYY-MM-DD"):
        app.save_order({"id": oid, "delivery_date": "2026-02-30"})
    assert row(oid)["delivery_date"] == DD


# ---------- S2 ----------

def test_s2_id_zero_updates_the_row_instead_of_creating():
    with app.db() as c:   # id=0 是合法主键，直接塞一行
        c.execute("INSERT INTO orders(id,item,status) VALUES(0,'納豆','ordered')")
    assert app.save_order({"id": 0, "item": "味噌"}) == 0
    assert row(0)["item"] == "味噌"
    assert len(all_rows()) == 1


def test_s2_null_or_empty_id_still_creates():
    assert app.save_order({"id": None, "item": "A"}) == 1
    assert app.save_order({"id": "", "item": "B"}) == 2
    assert [r["item"] for r in all_rows()] == ["A", "B"]


# ---------- S3 ----------

def test_s3_update_missing_id_raises_and_writes_nothing():
    oid = app.save_order({"item": "納豆", "delivery_date": DD})
    with pytest.raises(ValueError, match="不存在"):
        app.save_order({"id": 999, "item": "x"})
    assert [r["id"] for r in all_rows()] == [oid]      # 没有凭空多出一行


def test_s3_update_of_id_zero_that_does_not_exist_raises():
    with pytest.raises(ValueError, match="不存在"):
        app.save_order({"id": 0, "item": "x"})
    assert all_rows() == []


# ---------- S5 ----------

@pytest.mark.parametrize("bad", ["9:5", "12:3", "abc", "0900", "9:05:00", "25:00"])
def test_s5_due_rejects_every_window_start_save_order_rejects(bad):
    # save_order 一律拒这些；due() 过去只 split(":")，「9:5」被当 09:05 用
    with pytest.raises(ValueError):
        remind.due(order(window_start=bad), at(9, 0))


@pytest.mark.parametrize("good", ["9:05", "00:00", "23:59"])
def test_s5_due_still_accepts_valid_window_start(good):
    # 与 save_order 一致：这些合法，不该被新校验误拒
    assert isinstance(remind.due(order(window_start=good), at(9, 0)), list)


def test_s5_valid_window_start_still_fires_as_before():
    assert remind.due(order(window_start="11:00", window_end="13:00"), at(9, 0)) == [
        ("pre2h", 1, "今天 11:00 到 13:00 送 納豆，记得在家收货。")]

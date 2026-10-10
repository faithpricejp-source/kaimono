"""钉住 app.save_order() 现在的行为（建/改/清提醒/各类非法输入）。"""
from __future__ import annotations

import json

import pytest

import app

DD = "2026-10-08"


def row(oid) -> dict:
    with app.db() as c:
        r = c.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
    return dict(r) if r else {}


def all_rows() -> list[dict]:
    with app.db() as c:
        return [dict(r) for r in c.execute("SELECT * FROM orders")]


def reminders() -> set:
    with app.db() as c:
        return {(r["order_id"], r["kind"]) for r in c.execute("SELECT order_id, kind FROM reminders")}


def put_reminder(oid, kind="day"):
    with app.db() as c:
        c.execute("INSERT OR REPLACE INTO reminders(order_id, kind, channels) VALUES(?,?,?)", (oid, kind, "{}"))


def events() -> list[dict]:
    with app.db() as c:
        return [dict(r) for r in c.execute("SELECT kind, ref, payload FROM events ORDER BY id")]


# ---------- 新建 ----------

def test_create_returns_id_and_stores_stripped_values():
    oid = app.save_order({"merchant": " 楽天 ", "item": " 納豆 ", "status": "ordered"})
    assert oid == 1
    r = row(oid)
    assert r["merchant"] == "楽天" and r["item"] == "納豆" and r["status"] == "ordered"
    assert r["updated_ts"] is None and r["created_ts"]


def test_create_defaults_to_ordered_and_nulls():
    oid = app.save_order({})
    r = row(oid)
    assert r["status"] == "ordered"
    assert all(r[k] is None for k in app.ORDER_FIELDS if k != "status")


def test_create_coerces_non_string_values():
    oid = app.save_order({"item": 123, "order_no": 45})
    r = row(oid)
    assert r["item"] == "123" and r["order_no"] == "45"


def test_create_turns_blank_strings_into_null():
    oid = app.save_order({"merchant": "   ", "item": "", "status": "  "})
    r = row(oid)
    assert r["merchant"] is None and r["item"] is None
    assert r["status"] == "ordered"


def test_create_logs_order_add_event():
    oid = app.save_order({"item": "納豆"})
    ev = events()
    assert len(ev) == 1 and ev[0]["kind"] == "order_add" and ev[0]["ref"] == str(oid)
    assert json.loads(ev[0]["payload"])["item"] == "納豆"


# ---------- 非法输入 ----------

@pytest.mark.parametrize("bad", ["25:00", "24:00", "23:60", "9:5", "abc", "0900", "12:3", "9:05:00"])
def test_bad_window_start_rejected(bad):
    with pytest.raises(ValueError, match="window_start 要写成 HH:MM"):
        app.save_order({"window_start": bad})


def test_bad_window_end_rejected():
    with pytest.raises(ValueError, match="window_end 要写成 HH:MM"):
        app.save_order({"window_end": "25:30"})


@pytest.mark.parametrize("good", ["9:05", "00:00", "23:59"])
def test_good_window_accepted(good):
    assert app.save_order({"window_start": good, "window_end": good})


@pytest.mark.parametrize("bad", ["2026/10/06", "26-10-6", "2026-10-6", "Oct 6 2026", "2026-10-061"])
def test_bad_delivery_date_rejected(bad):
    with pytest.raises(ValueError, match="delivery_date 要写成 YYYY-MM-DD"):
        app.save_order({"delivery_date": bad})


def test_impossible_but_well_shaped_date_is_accepted():
    # bugfix-1007-S1：原来这条钉的是「只校验形状」的旧行为，现在改成钉被拒；测试名保留
    with pytest.raises(ValueError, match="delivery_date 要写成 YYYY-MM-DD"):
        app.save_order({"delivery_date": "2026-13-45"})
    assert all_rows() == []


@pytest.mark.parametrize("bad", ["bogus", "DELIVERED"])
def test_bad_status_rejected(bad):
    with pytest.raises(ValueError, match="bad status " + bad.strip()):
        app.save_order({"status": bad})


@pytest.mark.parametrize("good", ["ordered", "shipped", "delivered", "cancelled"])
def test_every_status_accepted(good):
    assert row(app.save_order({"status": good}))["status"] == good


def test_nothing_written_when_validation_fails():
    with pytest.raises(ValueError):
        app.save_order({"item": "x", "status": "nope"})
    assert all_rows() == []


# ---------- 更新 ----------

def test_update_touches_only_the_given_fields():
    oid = app.save_order({"item": "納豆", "merchant": "楽天", "status": "ordered"})
    app.save_order({"id": oid, "item": "味噌"})
    r = row(oid)
    assert r["item"] == "味噌" and r["merchant"] == "楽天" and r["status"] == "ordered"
    assert r["updated_ts"]


def test_update_logs_only_changed_fields():
    oid = app.save_order({"item": "納豆"})
    app.save_order({"id": oid, "item": "味噌"})
    ev = events()[-1]
    assert ev["kind"] == "order_update" and ev["ref"] == str(oid)
    assert json.loads(ev["payload"]) == {"item": "味噌"}


def test_update_with_no_known_field_raises():
    oid = app.save_order({"item": "納豆"})
    with pytest.raises(ValueError, match="没有要更新的字段"):
        app.save_order({"id": oid, "zzz": "q"})


def test_update_with_only_id_raises():
    with pytest.raises(ValueError, match="没有要更新的字段"):
        app.save_order({"id": 1})


def test_update_missing_order_is_silent():
    # bugfix-1007-S3：更新不存在的 id 不再静默成功（测试名保留，只换断言）
    with pytest.raises(ValueError, match="订单 999 不存在"):
        app.save_order({"id": 999, "item": "x"})
    assert all_rows() == []


def test_id_zero_creates_a_new_order_instead_of_updating():
    # bugfix-1007-S2：id=0 不再被当 falsy 走新建，而是走更新（测试名按要求保留，只换断言）
    with app.db() as c:
        c.execute("INSERT INTO orders(id,item,status) VALUES(0,'x','ordered')")
    assert app.save_order({"id": 0, "item": "y"}) == 0
    assert row(0)["item"] == "y"
    assert len(all_rows()) == 1


def test_update_validates_the_new_values():
    oid = app.save_order({"item": "納豆"})
    with pytest.raises(ValueError, match="delivery_date 要写成 YYYY-MM-DD"):
        app.save_order({"id": oid, "delivery_date": "2026/10/08"})
    assert row(oid)["delivery_date"] is None


# ---------- 改送达时间 → 清掉已发提醒 ----------

@pytest.mark.parametrize("field,value", [("delivery_date", DD), ("window_start", "10:00"),
                                         ("window_end", "12:00")])
def test_changing_delivery_time_clears_reminders(field, value):
    oid = app.save_order({"item": "納豆", "delivery_date": "2026-10-06", "window_start": "14:00"})
    put_reminder(oid, "day")
    put_reminder(oid, "pre2h")
    app.save_order({"id": oid, field: value})
    assert reminders() == set()


def test_changing_other_fields_keeps_reminders():
    oid = app.save_order({"item": "納豆", "delivery_date": "2026-10-06"})
    put_reminder(oid, "day")
    app.save_order({"id": oid, "note": "メモ"})
    assert reminders() == {(oid, "day")}


@pytest.mark.parametrize("start", ["9:00", "09:00"])
def test_form_edit_with_unchanged_delivery_keeps_reminders(start):
    oid = app.save_order({"item": "fixture", "delivery_date": "2026-10-06", "window_start": "9:00"})
    put_reminder(oid, "pre2h")
    app.save_order({"id": oid, "note": "changed only note", "delivery_date": "2026-10-06",
                    "window_start": start, "window_end": ""})
    assert app.list_orders()[0]["reminders"][0]["kind"] == "pre2h"


def test_clearing_reminders_with_null_delivery_date():
    # 键存在就清，哪怕值是 None
    oid = app.save_order({"item": "納豆", "delivery_date": "2026-10-06"})
    put_reminder(oid, "day")
    app.save_order({"id": oid, "delivery_date": None})
    assert reminders() == set()
    assert row(oid)["delivery_date"] is None


def test_reminders_of_other_orders_are_untouched():
    a = app.save_order({"item": "A", "delivery_date": "2026-10-06"})
    b = app.save_order({"item": "B", "delivery_date": "2026-10-06"})
    put_reminder(a, "day")
    put_reminder(b, "day")
    app.save_order({"id": a, "delivery_date": DD})
    assert reminders() == {(b, "day")}

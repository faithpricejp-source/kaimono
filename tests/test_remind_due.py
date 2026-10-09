"""钉住 remind.due() 现在的判定（不联网、不发提醒）。"""
from __future__ import annotations

import datetime as dt
import sys
import types

import pytest

import app
import remind

D = "2026-10-06"

# 下面几条钉的是 Python 3.14 起 datetime 的报错原文（如「hour must be in 0..23, not 25」）；
# 3.13 及以前的原文不同（「hour must be in 0..23」「day is out of range for month」），只在 3.14+ 上比对。
NEEDS_PY314_MSG = pytest.mark.skipif(sys.version_info < (3, 14),
                                     reason="断言的是 Python 3.14+ 的 datetime 报错原文，旧版本文案不同")


def order(**kw) -> dict:
    o = dict(id=1, merchant="楽天", item="納豆", delivery_date=D, window_start=None,
             window_end=None, status="ordered")
    o.update(kw)
    return o


def at(h: int, m: int = 0, sec: int = 0) -> dt.datetime:
    return dt.datetime(2026, 10, 6, h, m, sec)


# ---------- 只写了日期、没写时间窗 ----------

def test_no_window_fires_at_nine():
    assert remind.due(order(), at(9, 0)) == [
        ("day", 1, "今天有快递：納豆，记得在家收货。")]


def test_no_window_silent_before_nine():
    assert remind.due(order(), at(8, 59)) == []


def test_no_window_grace_is_two_hours():
    assert len(remind.due(order(), at(11, 0))) == 1
    assert remind.due(order(), at(11, 0, 1)) == []


def test_empty_window_start_counts_as_no_window():
    assert remind.due(order(window_start=""), at(9, 0)) == [
        ("day", 1, "今天有快递：納豆，记得在家收货。")]


def test_no_delivery_date_never_fires():
    assert remind.due(order(delivery_date=None), at(9, 0)) == []


def test_other_day_never_fires():
    for day in ("2026-10-05", "2026-10-07", "2027-10-06"):
        assert remind.due(order(delivery_date=day), at(9, 0)) == []


# ---------- 状态 ----------

@pytest.mark.parametrize("status", ["delivered", "cancelled"])
def test_finished_status_never_fires(status):
    assert remind.due(order(status=status), at(9, 0)) == []


def test_shipped_fires_like_ordered():
    assert remind.due(order(status="shipped"), at(9, 0)) == [
        ("day", 1, "今天有快递：納豆，记得在家收货。")]


@pytest.mark.parametrize("status", [None, "", "unknown", "ordered "])
def test_non_active_status_never_fires(status):
    assert remind.due(order(status=status), at(9, 0)) == []


# ---------- 有时间段：前 2 小时 / 前 20 分钟 ----------

WIN = dict(window_start="14:00", window_end="16:00")
PRE2H = ("pre2h", 1, "今天 14:00 到 16:00 送 納豆，记得在家收货。")
PRE20M = ("pre20m", 3, "今天 14:00 到 16:00 送 納豆，记得在家收货。")


def test_pre2h_window_boundaries():
    assert remind.due(order(**WIN), at(11, 59)) == []
    assert remind.due(order(**WIN), at(12, 0)) == [PRE2H]
    assert remind.due(order(**WIN), at(12, 15)) == [PRE2H]
    assert remind.due(order(**WIN), at(12, 16)) == []


def test_pre20m_window_boundaries():
    assert remind.due(order(**WIN), at(13, 39)) == []
    assert remind.due(order(**WIN), at(13, 40)) == [PRE20M]
    assert remind.due(order(**WIN), at(13, 55)) == [PRE20M]
    assert remind.due(order(**WIN), at(13, 56)) == []


def test_at_window_start_nothing_fires():
    assert remind.due(order(**WIN), at(14, 0)) == []


def test_only_one_kind_fires_at_a_time():
    for t in (at(12, 0), at(12, 15), at(13, 40), at(13, 55)):
        assert len(remind.due(order(**WIN), t)) == 1


def test_pre20m_says_three_times():
    assert remind.due(order(**WIN), at(13, 40))[0][1] == 3


def test_window_without_end_omits_range():
    assert remind.due(order(window_start="14:00"), at(12, 0)) == [
        ("pre2h", 1, "今天 14:00 送 納豆，记得在家收货。")]


# ---------- 文案里叫它什么 ----------

def test_item_wins_over_merchant():
    assert remind.due(order(item="味噌", merchant="Amazon"), at(9, 0))[0][2] == \
        "今天有快递：味噌，记得在家收货。"


def test_falls_back_to_merchant():
    assert remind.due(order(item=None, merchant="Amazon"), at(9, 0))[0][2] == \
        "今天有快递：Amazon，记得在家收货。"


@pytest.mark.parametrize("item,merchant", [(None, None), ("", ""), (None, "")])
def test_falls_back_to_generic_word(item, merchant):
    assert remind.due(order(item=item, merchant=merchant), at(9, 0))[0][2] == \
        "今天有快递：快递，记得在家收货。"


# ---------- 非法输入：现在会抛 ValueError ----------

@pytest.mark.parametrize("bad,msg", [
    pytest.param("25:00", "hour must be in 0..23, not 25", marks=NEEDS_PY314_MSG),
    pytest.param("24:00", "hour must be in 0..23, not 24", marks=NEEDS_PY314_MSG),
    pytest.param("23:60", "minute must be in 0..59, not 60", marks=NEEDS_PY314_MSG),
])
def test_bad_window_start_raises(bad, msg):
    with pytest.raises(ValueError, match=msg):
        remind.due(order(window_start=bad), at(9, 0))


def test_unparsable_window_start_raises():
    # bugfix-1007-S5：原来这错来自 int("abc") 的副作用，现在是 due() 自己按 save_order 的形状规则抛
    with pytest.raises(ValueError, match="window_start abc 要写成 HH:MM"):
        remind.due(order(window_start="abc"), at(9, 0))


@pytest.mark.parametrize("bad,msg", [
    ("2026-13-45", "month must be in 1..12"),
    pytest.param("2026-10-32", "day 32 must be in range 1..31 for month 10", marks=NEEDS_PY314_MSG),
    ("10/06/2026", "Invalid isoformat string"),
    ("tomorrow", "Invalid isoformat string"),
])
def test_bad_delivery_date_raises(bad, msg):
    with pytest.raises(ValueError, match=msg):
        remind.due(order(delivery_date=bad), at(9, 0))


def test_single_digit_minute_is_accepted_as_09_05():
    # bugfix-1007-S5：原来钉的是 due() 只 split(":") 就把 "9:5" 当 09:05 算；现在与 save_order 一致拒掉（测试名保留）
    with pytest.raises(ValueError, match="window_start 9:5 要写成 HH:MM"):
        remind.due(order(window_start="9:5"), at(9, 0))


# ---------- main()：一条脏数据不影响其余订单，同类提醒只发一次 ----------

def freeze(monkeypatch, now: dt.datetime):
    class Fixed(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    fake = types.ModuleType("fake_dt")
    fake.datetime, fake.date, fake.time, fake.timedelta = Fixed, dt.date, dt.time, dt.timedelta
    monkeypatch.setattr(remind, "dt", fake)


def seed(*rows):
    with app.db() as c:
        for r in rows:
            c.execute("""INSERT INTO orders(item,status,delivery_date,window_start,window_end)
                         VALUES(:item,:status,:delivery_date,:window_start,:window_end)""", r)


@NEEDS_PY314_MSG
def test_main_sends_each_kind_once_and_skips_dirty_row(monkeypatch, capsys):
    seed(dict(item="AAA", status="ordered", delivery_date=D, window_start=None, window_end=None),
         dict(item="BBB", status="ordered", delivery_date=D, window_start="14:00", window_end="16:00"),
         dict(item="CCC", status="ordered", delivery_date=D, window_start="25:00", window_end=None))
    sent = []
    monkeypatch.setattr(remind, "send_alert",
                        lambda text, title="收货提醒", say_times=1: (sent.append((text, say_times)),
                                                                {"notification": True, "say": True})[1])
    freeze(monkeypatch, at(12, 5))

    assert remind.main() == 0
    assert sent == [("今天 14:00 到 16:00 送 BBB，记得在家收货。", 1)]
    with app.db() as c:
        assert {(r["order_id"], r["kind"]) for r in c.execute("SELECT order_id, kind FROM reminders")} == {(2, "pre2h")}
        ev = [dict(r) for r in c.execute("SELECT ref, payload FROM events WHERE kind='reminder'")]
    assert len(ev) == 1 and ev[0]["ref"] == "2"
    assert "order 3 skipped: hour must be in 0..23, not 25" in capsys.readouterr().out

    sent.clear()
    assert remind.main() == 0
    assert sent == []


def test_main_leaves_delivered_orders_alone(monkeypatch):
    seed(dict(item="AAA", status="delivered", delivery_date=D, window_start="14:00", window_end="16:00"))
    sent = []
    monkeypatch.setattr(remind, "send_alert", lambda text, title="收货提醒", say_times=1: sent.append(text))
    freeze(monkeypatch, at(12, 5))
    assert remind.main() == 0 and sent == []

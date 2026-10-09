"""remind.main() 的版本无关回归：test_remind_due 里同名用例要比对 3.14+ 的报错原文，在 3.12/3.13 上跳过；
这里钉同样的行为（脏数据跳过、其余照发、同类只发一次），只是不比对 datetime 报错原文。"""
from __future__ import annotations

import datetime as dt
import types

import app
import remind

D = "2026-10-06"


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


def test_main_skips_dirty_row_and_sends_the_rest_once(monkeypatch, capsys):
    seed(dict(item="AAA", status="ordered", delivery_date=D, window_start="14:00", window_end=None),
         dict(item="BBB", status="ordered", delivery_date=D, window_start="25:00", window_end=None),
         dict(item="CCC", status="shipped", delivery_date=D, window_start="12:20", window_end="14:00"))
    sent = []
    monkeypatch.setattr(remind, "send_alert",
                        lambda text, title="收货提醒", say_times=1: (sent.append((text, say_times)), {"say": True})[1])
    freeze(monkeypatch, dt.datetime(2026, 10, 6, 12, 5))

    assert remind.main() == 0
    assert sent == [("今天 14:00 送 AAA，记得在家收货。", 1), ("今天 12:20 到 14:00 送 CCC，记得在家收货。", 3)]
    with app.db() as c:
        assert {(r["order_id"], r["kind"]) for r in c.execute("SELECT order_id, kind FROM reminders")} == \
            {(1, "pre2h"), (3, "pre20m")}
        assert [r["ref"] for r in c.execute("SELECT ref FROM events WHERE kind='reminder' ORDER BY id")] == ["1", "3"]
    out = capsys.readouterr().out
    assert "order 2 skipped: hour must be in 0..23" in out      # 3.12–3.14 共有的前缀

    sent.clear()
    assert remind.main() == 0
    assert sent == []

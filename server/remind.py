"""收货提醒：每 5 分钟由 launchd 跑一次，给今天要送达的订单发提醒。

- 时间窗开始前 2 小时：系统通知 + Mac 喇叭念一遍 + 邮件
- 时间窗开始前 20 分钟：同上，喇叭念三遍
- 只写了日期没写时间窗的：当天 09:00 提醒一次
每条订单每种提醒只发一次（reminders 表）；改了送达时间会清掉重发。
提醒通道将来可加 ntfy 等推送到随身设备。
"""
from __future__ import annotations

import datetime as dt
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import app  # noqa: E402

PLAN = [("pre2h", dt.timedelta(hours=2), 1), ("pre20m", dt.timedelta(minutes=20), 3)]
GRACE = dt.timedelta(minutes=15)   # 到点后 15 分钟内补发（机器睡着/任务延迟时不至于漏）


def send_alert(text: str, title: str = "收货提醒", say_times: int = 1) -> dict:
    res = {}
    safe = text.replace('"', "'")
    res["notification"] = subprocess.run(
        ["osascript", "-e", f'display notification "{safe}" with title "{title}" sound name "Glass"'],
        capture_output=True).returncode == 0
    ok = True
    for _ in range(say_times):
        ok &= subprocess.run(["say", "-v", os.environ.get("SHOPPING_SAY_VOICE", "Tingting"), text], capture_output=True).returncode == 0
    res["say"] = ok
    try:
        from notify import send_email  # 可选：自备一个提供 send_email(subject, body) 的 notify.py 放进 PYTHONPATH
        res["email"] = bool(send_email(f"[{title}] {text[:40]}", text))
    except Exception as e:  # noqa: BLE001
        res["email"] = False
        res["email_error"] = str(e)[:200]
    return res


def due(order: dict, now: dt.datetime) -> list[tuple[str, int, str]]:
    if order["status"] not in ("ordered", "shipped") or not order["delivery_date"]:
        return []
    day = dt.date.fromisoformat(order["delivery_date"])
    if day != now.date():
        return []
    what = order["item"] or order["merchant"] or "快递"
    if not order["window_start"]:
        at = dt.datetime.combine(day, dt.time(9, 0))
        return [("day", 1, f"今天有快递：{what}，记得在家收货。")] if at <= now <= at + GRACE * 8 else []
    ws = order["window_start"]
    if not re.fullmatch(r"\d{1,2}:\d{2}", ws):   # bugfix-1007-S5 与 save_order 的 HH:MM 校验对齐：解析不了就抛
        raise ValueError(f"window_start {ws} 要写成 HH:MM（00:00-23:59，深夜 25:00 请写成次日日期）")
    h, m = map(int, ws.split(":"))
    start = dt.datetime.combine(day, dt.time(h, m))
    win = f"{order['window_start']}" + (f" 到 {order['window_end']}" if order["window_end"] else "")
    out = []
    for kind, ahead, times in PLAN:
        at = start - ahead
        if at <= now <= at + GRACE:
            out.append((kind, times, f"今天 {win} 送 {what}，记得在家收货。"))
    return out


def main() -> int:
    now = dt.datetime.now()
    sent_any = 0
    with app.db() as c:
        done = {(r["order_id"], r["kind"]) for r in c.execute("SELECT order_id, kind FROM reminders")}
        orders = [dict(r) for r in c.execute("SELECT * FROM orders")]
    for o in orders:
        try:
            plan = due(o, now)
        except ValueError as e:  # 一条脏数据（如 25:00）不能拖垮其余订单的提醒
            print(f"{now:%F %T} order {o['id']} skipped: {e}", flush=True)
            continue
        for kind, times, text in plan:
            if (o["id"], kind) in done:
                continue
            res = send_alert(text, say_times=times)
            with app.db() as c:
                c.execute("INSERT OR REPLACE INTO reminders(order_id, kind, channels) VALUES(?,?,?)",
                          (o["id"], kind, str(res)))
                app.log_event(c, "reminder", o["id"], {"kind": kind, "text": text, **res})
            print(f"{now:%F %T} order {o['id']} {kind}: {res}", flush=True)
            sent_any += 1
    return 0


if __name__ == "__main__":
    app.init_db()
    sys.exit(main())

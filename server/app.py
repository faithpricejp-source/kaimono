"""「买东西」服务：订单与收货提醒、盯货清单（读 price-sentinel 等现有哨兵的状态）、荐商品（待做）。标准库实现。

运行：python3 server/app.py
只监听 127.0.0.1:8470，需要在其他设备上用时，可经自己的内网（如 tailscale serve）转发。
数据：~/Library/Application Support/shopping/shop.sqlite（Time Machine 覆盖）。
"""
from __future__ import annotations

import datetime as dt
import json
import mimetypes
import os
import re
import sqlite3
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("SHOPPING_DATA", "~/Library/Application Support/shopping")).expanduser()
DB_PATH = DATA_DIR / "shop.sqlite"
PORT = int(os.environ.get("SHOPPING_PORT", "8470"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_ts TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  merchant TEXT, item TEXT, order_no TEXT, ordered_at TEXT,
  delivery_date TEXT, window_start TEXT, window_end TEXT,
  carrier TEXT, tracking_no TEXT,
  status TEXT NOT NULL DEFAULT 'ordered',   -- ordered | shipped | delivered | cancelled
  source TEXT, note TEXT, updated_ts TEXT);
CREATE TABLE IF NOT EXISTS reminders(
  order_id INTEGER, kind TEXT, sent_ts TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  channels TEXT, PRIMARY KEY(order_id, kind));
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  kind TEXT NOT NULL, ref TEXT, payload TEXT);
"""
ORDER_FIELDS = ["merchant", "item", "order_no", "ordered_at", "delivery_date", "window_start", "window_end",
                "carrier", "tracking_no", "status", "source", "note"]
STATUSES = {"ordered", "shipped", "delivered", "cancelled"}

# 外部哨兵登记（只读：读它们的 launchd 状态、state 文件、最后一行日志，不改它们）。
# 清单放在数据目录的 watchers.json，格式见仓库里的 watchers.example.json；没有这个文件就不显示哨兵。
def load_watchers() -> list[dict]:
    p = DATA_DIR / "watchers.json"
    if not p.exists():
        return []
    out = []
    for w in json.loads(p.read_text(encoding="utf-8")):
        out.append({"name": w["name"], "label": w.get("label"),
                    "state": Path(w["state"]).expanduser() if w.get("state") else None,
                    "log": Path(w["log"]).expanduser() if w.get("log") else None})
    return out


WATCHERS = load_watchers()


def db() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def init_db() -> None:
    with db() as c:
        c.executescript(SCHEMA)


def log_event(c, kind: str, ref=None, payload=None) -> None:
    c.execute("INSERT INTO events(kind, ref, payload) VALUES(?,?,?)",
              (kind, None if ref is None else str(ref), json.dumps(payload, ensure_ascii=False) if payload else None))


def list_orders() -> list[dict]:
    with db() as c:
        rows = c.execute("""SELECT * FROM orders ORDER BY
            CASE status WHEN 'delivered' THEN 2 WHEN 'cancelled' THEN 3 ELSE 1 END,
            COALESCE(delivery_date, '9999'), id DESC""").fetchall()
        sent = {}
        for r in c.execute("SELECT order_id, kind, sent_ts FROM reminders"):
            sent.setdefault(r["order_id"], []).append({"kind": r["kind"], "ts": r["sent_ts"]})
    return [{**dict(r), "reminders": sent.get(r["id"], [])} for r in rows]


def save_order(req: dict) -> int:
    vals = {k: (str(req[k]).strip() or None) if req.get(k) is not None else None for k in ORDER_FIELDS}
    if vals.get("status") and vals["status"] not in STATUSES:
        raise ValueError(f"bad status {vals['status']}")
    for k in ("window_start", "window_end"):
        m = re.fullmatch(r"(\d{1,2}):(\d{2})", vals[k]) if vals.get(k) else None
        if vals.get(k) and not (m and int(m[1]) <= 23 and int(m[2]) <= 59):
            raise ValueError(f"{k} 要写成 HH:MM（00:00–23:59，深夜 25:00 请写成次日日期）")
    if vals.get("delivery_date"):
        ok = bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", vals["delivery_date"]))
        if ok:
            try:
                dt.date.fromisoformat(vals["delivery_date"])   # bugfix-1007-S1 形状对但没这一天（2026-13-45）
            except ValueError:
                ok = False
        if not ok:
            raise ValueError("delivery_date 要写成 YYYY-MM-DD")
    with db() as c:
        if "id" in req and req["id"] not in (None, ""):   # bugfix-1007-S2 id=0 是 falsy，过去走新建分支
            oid = int(req["id"])
            previous = c.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
            sets = ", ".join(f"{k}=?" for k in ORDER_FIELDS if k in req)
            if not sets:
                raise ValueError("没有要更新的字段")
            args = [vals[k] for k in ORDER_FIELDS if k in req]
            cur = c.execute(f"UPDATE orders SET {sets}, updated_ts=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id=?",
                            args + [oid])
            if cur.rowcount == 0:
                raise ValueError(f"订单 {oid} 不存在")   # bugfix-1007-S3 更新不存在的 id 不再静默成功
            # 改了送达时间 → 之前发过的提醒作废，按新时间重新提醒
            def delivery_value(k, value):
                return value.zfill(5) if value and k in ("window_start", "window_end") else value or None
            if any(k in req and delivery_value(k, vals[k]) != delivery_value(k, previous[k])
                   for k in ("delivery_date", "window_start", "window_end")):
                c.execute("DELETE FROM reminders WHERE order_id=?", (oid,))
            log_event(c, "order_update", oid, {k: vals[k] for k in ORDER_FIELDS if k in req})
        else:
            vals["status"] = vals.get("status") or "ordered"
            oid = c.execute(f"INSERT INTO orders({','.join(ORDER_FIELDS)}) VALUES({','.join('?' * len(ORDER_FIELDS))})",
                            [vals[k] for k in ORDER_FIELDS]).lastrowid
            log_event(c, "order_add", oid, vals)
    return oid


def _launchd(label: str) -> dict:
    p = subprocess.run(["launchctl", "list", label], capture_output=True, text=True)
    if p.returncode != 0:
        return {"loaded": False}
    m = re.search(r'"LastExitStatus" = (-?\d+);', p.stdout)
    pid = re.search(r'"PID" = (\d+);', p.stdout)
    return {"loaded": True, "last_exit": int(m.group(1)) if m else None, "running": bool(pid)}


def _tail(path: Path | None) -> str | None:
    if not path or not path.exists():
        return None
    with path.open("rb") as f:
        f.seek(max(0, path.stat().st_size - 2000))
        lines = f.read().decode("utf-8", "ignore").strip().splitlines()
    return lines[-1][:300] if lines else None


def list_watchers() -> list[dict]:
    out = []
    for w in WATCHERS:
        state = None
        try:
            state = json.loads(w["state"].read_text()) if w["state"].exists() else None
        except ValueError:
            state = {"error": "state 文件读不了"}
        armed = state.get("armed") if isinstance(state, dict) else None
        out.append({"name": w["name"], "label": w["label"], "launchd": _launchd(w["label"]),
                    "armed": armed, "state": state if isinstance(state, dict) and len(json.dumps(state)) < 2000 else None,
                    "last_log": _tail(w["log"])})
    return out


_job: dict = {"thread": None, "kind": None, "error": None}


def start_job(kind: str) -> bool:
    import threading
    import recommend
    t = _job["thread"]
    if t and t.is_alive():
        return False
    def run():
        try:
            _job["error"] = None
            recommend.generate() if kind == "generate" else recommend.distill()
        except Exception as e:  # noqa: BLE001
            _job["error"] = f"{kind}: {e}"
    _job.update(kind=kind, thread=threading.Thread(target=run, daemon=True))
    _job["thread"].start()
    return True


def recs_state() -> dict:
    import recommend
    with recommend._db() as c:
        b = c.execute("SELECT * FROM rec_batches WHERE status='done' ORDER BY id DESC LIMIT 1").fetchone()
        n = c.execute("SELECT count(*) FROM rec_batches WHERE status='done'").fetchone()[0]
        recs = c.execute("SELECT * FROM recs WHERE batch_id=? ORDER BY pos", (b["id"],)).fetchall() if b else []
        fb = {}
        for r in c.execute("""SELECT ref, json_extract(payload,'$.verdict') v, json_extract(payload,'$.comment') cm
                              FROM events WHERE kind='rec_feedback' ORDER BY id"""):
            fb[r["ref"]] = {"verdict": r["v"], "comment": r["cm"]}
    t = _job["thread"]
    return {"batch": dict(b) if b else None, "batches": n,
            "recs": [{**dict(r), "feedback": fb.get(str(r["id"]))} for r in recs],
            "running": _job["kind"] if t and t.is_alive() else None, "error": _job["error"],
            "memo": recommend.MEMO.read_text() if recommend.MEMO.exists() else "",
            "profile": recommend.PROFILE.read_text() if recommend.PROFILE.exists() else recommend.PROFILE_SEED}


class Handler(BaseHTTPRequestHandler):
    server_version = "shopping/0.1"

    def log_message(self, fmt, *args):
        sys.stderr.write(f"{time.strftime('%H:%M:%S')} {fmt % args}\n")

    def _send(self, code, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def _cross_site(self) -> str | None:
        """跨站请求防护（2026-10-09，云端审查发现）：不拦的话，浏览器里任意网页都能用 text/plain 的
        「简单请求」POST 到 127.0.0.1 或 tailnet 地址，替用户改订单、写画像、触发花钱的推荐任务。
        规则：① 只收 application/json（跨站发 JSON 必须先预检，本服务不答预检，浏览器就会拦下）；
        ② 带 Origin 头时，Origin 的主机必须和 Host 头一致；③ Host 只认本机与 Tailscale 名字（挡 DNS 重绑定）。"""
        host = (self.headers.get("Host") or "").lower()
        hostname = host.rsplit(":", 1)[0] if not host.startswith("[") else host
        if hostname not in ("127.0.0.1", "localhost", "[::1]") and not hostname.endswith(".ts.net"):
            return f"host not allowed: {host}"
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype != "application/json":
            return "content-type must be application/json"
        origin = self.headers.get("Origin")
        if origin and urlparse(origin).netloc.lower() != host:
            return f"cross-origin request refused: {origin}"
        return None

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n < 0 or n > 50 * 1024 * 1024:
            raise ValueError("bad Content-Length")
        data = json.loads(self.rfile.read(n) or b"{}")
        if not isinstance(data, dict):
            raise ValueError("request body must be a JSON object")
        return data

    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path in ("/", "/index.html"):
                return self._static(ROOT / "web" / "index.html")
            if path.startswith("/web/"):
                f = (ROOT / "web" / path[5:]).resolve()
                if (ROOT / "web").resolve() not in f.parents:
                    return self._json({"error": "forbidden"}, 403)
                return self._static(f)
            if path == "/api/orders":
                return self._json({"orders": list_orders()})
            if path == "/api/recs":
                return self._json(recs_state())
            if path == "/api/watchers":
                return self._json({"watchers": list_watchers()})
            self._json({"error": "not found"}, 404)
        except Exception as e:  # noqa: BLE001
            self._json({"error": str(e)}, 500)

    def do_POST(self):
        path = urlparse(self.path).path
        bad = self._cross_site()
        if bad:
            return self._json({"error": bad}, 403)
        try:
            req = self._body()
            if path == "/api/orders":
                return self._json({"id": save_order(req)})
            if path in ("/api/recs/generate", "/api/recs/distill"):
                return self._json({"started": start_job(path.rsplit("/", 1)[1]), **recs_state()})
            if path == "/api/recs/feedback":
                with db() as c:
                    log_event(c, "rec_feedback", int(req["rec_id"]),
                              {"verdict": req["verdict"], "comment": (req.get("comment") or "").strip() or None})
                return self._json({"ok": True})
            if path == "/api/recs/comment":
                with db() as c:
                    log_event(c, "rec_comment", None, {"text": str(req.get("text") or "")[:2000]})
                return self._json({"ok": True})
            if path in ("/api/recs/memo", "/api/recs/profile"):
                import recommend
                f = recommend.MEMO if path.endswith("memo") else recommend.PROFILE
                f.write_text(str(req.get("text") or ""))
                with db() as c:
                    log_event(c, path.rsplit("/", 1)[1] + "_edit", None, {"chars": len(str(req.get("text") or ""))})
                return self._json({"ok": True})
            if path == "/api/remind/test":
                import remind
                return self._json(remind.send_alert("测试：买东西 App 的提醒能响。", title="测试提醒"))
            self._json({"error": "not found"}, 404)
        except ValueError as e:
            self._json({"error": str(e)}, 400)
        except Exception as e:  # noqa: BLE001
            self._json({"error": str(e)}, 500)

    def _static(self, f: Path):
        if not f.is_file():
            return self._json({"error": "not found"}, 404)
        ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        if f.suffix == ".js":
            ctype = "text/javascript"
        self._send(200, f.read_bytes(), ctype + ("; charset=utf-8" if ctype.startswith("text") else ""))


def main():
    sys.path.insert(0, str(Path(__file__).parent))
    init_db()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"shopping on http://127.0.0.1:{PORT}  db={DB_PATH}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()

"""钉住 app.Handler 的 do_GET / do_POST 路由（不开端口：请求写进内存里的 rfile，响应从 wfile 读回）。"""
from __future__ import annotations

import io
import json

import pytest

import app
import recommend
import remind


def call(method: str, path: str, body: bytes | dict | None = None) -> tuple[int, dict, bytes]:
    data = json.dumps(body, ensure_ascii=False).encode() if isinstance(body, dict) else (body or b"")
    # POST 要过跨站防护：本机 Host + application/json（见 test_cross_site.py）
    ctype = "Content-Type: application/json\r\n" if method == "POST" else ""
    raw = (f"{method} {path} HTTP/1.1\r\nHost: 127.0.0.1\r\n{ctype}"
           f"Content-Length: {len(data)}\r\n\r\n").encode() + data
    h = app.Handler.__new__(app.Handler)
    h.rfile, h.wfile = io.BytesIO(raw), io.BytesIO()
    h.client_address, h.server, h.request = ("127.0.0.1", 0), None, None
    h.handle_one_request()
    head, _, payload = h.wfile.getvalue().partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    headers = dict(line.split(": ", 1) for line in lines[1:])
    return int(lines[0].split()[1]), headers, payload


def get_json(path: str) -> tuple[int, dict]:
    code, _, payload = call("GET", path)
    return code, json.loads(payload)


def post_json(path: str, body: bytes | dict | None = None) -> tuple[int, dict]:
    code, _, payload = call("POST", path, body)
    return code, json.loads(payload)


def events(kind: str) -> list[dict]:
    with app.db() as c:
        return [dict(r) for r in c.execute("SELECT ref, payload FROM events WHERE kind=? ORDER BY id", (kind,))]


# ---------- GET：静态文件 ----------

@pytest.mark.parametrize("path", ["/", "/index.html"])
def test_get_root_serves_index_html(path):
    code, headers, payload = call("GET", path)
    assert code == 200
    assert headers["Content-Type"] == "text/html; charset=utf-8"
    assert headers["Cache-Control"] == "no-cache"
    assert payload == (app.ROOT / "web" / "index.html").read_bytes()
    assert int(headers["Content-Length"]) == len(payload)


def test_get_query_string_is_ignored_for_routing():
    assert call("GET", "/?v=1")[0] == 200


@pytest.mark.parametrize("name,ctype", [("style.css", "text/css; charset=utf-8"),
                                        ("theme.js", "text/javascript; charset=utf-8"),
                                        ("icon-192.png", "image/png")])
def test_get_web_file_content_types(name, ctype):
    code, headers, payload = call("GET", "/web/" + name)
    assert code == 200 and headers["Content-Type"] == ctype
    assert payload == (app.ROOT / "web" / name).read_bytes()


def test_get_web_missing_file_is_404():
    assert get_json("/web/nope.css") == (404, {"error": "not found"})


def test_get_web_directory_is_404():
    # 在 web/ 里面但不是文件 → 404（web/ 本身见下一条，是 403）
    assert get_json("/web/sub/")[0] == 404


@pytest.mark.parametrize("path", ["/web/../server/app.py", "/web/../../etc/passwd", "/web/"])
def test_get_web_path_outside_web_dir_is_forbidden(path):
    assert get_json(path) == (403, {"error": "forbidden"})


# ---------- GET：API ----------

def test_get_orders_lists_saved_orders_with_reminders():
    oid = app.save_order({"item": "納豆", "delivery_date": "2026-10-06"})
    with app.db() as c:
        c.execute("INSERT INTO reminders(order_id, kind, channels) VALUES(?,?,?)", (oid, "day", "{}"))
    code, body = get_json("/api/orders")
    assert code == 200
    [o] = body["orders"]
    assert o["id"] == oid and o["item"] == "納豆"
    assert [r["kind"] for r in o["reminders"]] == ["day"]


def test_get_orders_puts_open_before_delivered_before_cancelled():
    a = app.save_order({"item": "A", "status": "cancelled"})
    b = app.save_order({"item": "B", "status": "delivered"})
    c = app.save_order({"item": "C", "status": "shipped"})
    assert [o["id"] for o in get_json("/api/orders")[1]["orders"]] == [c, b, a]


def test_get_recs_when_empty():
    code, body = get_json("/api/recs")
    assert code == 200
    assert body["batch"] is None and body["batches"] == 0 and body["recs"] == []
    assert body["running"] is None and body["error"] is None
    assert body["memo"] == "" and body["profile"] == recommend.PROFILE_SEED


def test_get_recs_returns_latest_done_batch_with_feedback():
    with recommend._db() as c:
        c.execute("INSERT INTO rec_batches(status) VALUES('done')")
        bid = c.execute("INSERT INTO rec_batches(status, note) VALUES('done', 'N')").lastrowid
        c.execute("INSERT INTO rec_batches(status) VALUES('failed')")
        rid = c.execute("INSERT INTO recs(batch_id,pos,name) VALUES(?,0,'梨')", (bid,)).lastrowid
        app.log_event(c, "rec_feedback", rid, {"verdict": "love", "comment": "好き"})
    recommend.MEMO.write_text("MEMO")
    code, body = get_json("/api/recs")
    assert code == 200
    assert body["batch"]["id"] == bid and body["batches"] == 2
    assert [r["name"] for r in body["recs"]] == ["梨"]
    assert body["recs"][0]["feedback"] == {"verdict": "love", "comment": "好き"}
    assert body["memo"] == "MEMO"


def test_get_watchers_empty_without_watchers_json():
    assert get_json("/api/watchers") == (200, {"watchers": []})


def test_get_unknown_path_is_404():
    assert get_json("/api/nope") == (404, {"error": "not found"})


def test_get_internal_error_is_500(monkeypatch):
    def broken():
        raise RuntimeError("db gone")
    monkeypatch.setattr(app, "list_orders", broken)
    assert get_json("/api/orders") == (500, {"error": "db gone"})


# ---------- POST /api/orders ----------

def test_post_orders_creates_and_returns_id():
    code, body = post_json("/api/orders", {"item": "納豆", "merchant": "楽天"})
    assert (code, body) == (200, {"id": 1})
    assert get_json("/api/orders")[1]["orders"][0]["merchant"] == "楽天"


def test_post_orders_validation_error_is_400():
    code, body = post_json("/api/orders", {"window_start": "25:00"})
    assert code == 400 and body["error"].startswith("window_start 要写成 HH:MM")


def test_post_bad_json_is_400():
    code, body = post_json("/api/orders", b"{not json")
    assert code == 400 and "error" in body


def test_post_empty_body_is_treated_as_empty_object():
    assert post_json("/api/orders") == (200, {"id": 1})


# ---------- POST /api/recs/* ----------

@pytest.mark.parametrize("kind", ["generate", "distill"])
def test_post_recs_job_starts_the_matching_function(monkeypatch, kind):
    ran = []
    monkeypatch.setattr(recommend, "generate", lambda: ran.append("generate"))
    monkeypatch.setattr(recommend, "distill", lambda: ran.append("distill"))
    code, body = post_json(f"/api/recs/{kind}", {})
    app._job["thread"].join(5)
    assert code == 200 and body["started"] is True
    assert ran == [kind]
    assert app._job["kind"] == kind and app._job["error"] is None


def test_post_recs_job_records_error_from_the_job(monkeypatch):
    def fail():
        raise RuntimeError("claude down")
    monkeypatch.setattr(recommend, "generate", fail)
    post_json("/api/recs/generate", {})
    app._job["thread"].join(5)
    assert app._job["error"] == "generate: claude down"
    assert get_json("/api/recs")[1]["error"] == "generate: claude down"


def test_post_recs_job_not_started_while_one_is_running(monkeypatch):
    class Busy:
        def is_alive(self):
            return True
    monkeypatch.setitem(app._job, "thread", Busy())
    monkeypatch.setitem(app._job, "kind", "distill")
    code, body = post_json("/api/recs/generate", {})
    assert code == 200 and body["started"] is False and body["running"] == "distill"


def test_post_feedback_logs_event_and_strips_comment():
    assert post_json("/api/recs/feedback", {"rec_id": "7", "verdict": "no", "comment": "  高い "}) == (200, {"ok": True})
    [ev] = events("rec_feedback")
    assert ev["ref"] == "7" and json.loads(ev["payload"]) == {"verdict": "no", "comment": "高い"}


def test_post_feedback_blank_comment_becomes_null():
    post_json("/api/recs/feedback", {"rec_id": 7, "verdict": "love", "comment": "   "})
    assert json.loads(events("rec_feedback")[0]["payload"]) == {"verdict": "love", "comment": None}


def test_post_feedback_missing_field_is_500():
    code, body = post_json("/api/recs/feedback", {"verdict": "no"})
    assert code == 500 and "rec_id" in body["error"]
    assert events("rec_feedback") == []


def test_post_feedback_non_integer_rec_id_is_400():
    assert post_json("/api/recs/feedback", {"rec_id": "x", "verdict": "no"})[0] == 400


def test_post_comment_logs_text_capped_at_2000_chars():
    assert post_json("/api/recs/comment", {"text": "あ" * 2500}) == (200, {"ok": True})
    [ev] = events("rec_comment")
    assert ev["ref"] is None and json.loads(ev["payload"]) == {"text": "あ" * 2000}


def test_post_comment_without_text_logs_empty_text():
    post_json("/api/recs/comment", {})
    assert json.loads(events("rec_comment")[0]["payload"]) == {"text": ""}


@pytest.mark.parametrize("which", ["memo", "profile"])
def test_post_memo_and_profile_write_file_and_log(which):
    assert post_json(f"/api/recs/{which}", {"text": "本文"}) == (200, {"ok": True})
    f = recommend.MEMO if which == "memo" else recommend.PROFILE
    assert f.read_text() == "本文"
    [ev] = events(which + "_edit")
    assert json.loads(ev["payload"]) == {"chars": 2}


def test_post_memo_without_text_writes_empty_file():
    post_json("/api/recs/memo", {})
    assert recommend.MEMO.read_text() == ""
    assert json.loads(events("memo_edit")[0]["payload"]) == {"chars": 0}


# ---------- POST /api/remind/test 与兜底 ----------

def test_post_remind_test_returns_alert_result(monkeypatch):
    sent = []
    monkeypatch.setattr(remind, "send_alert",
                        lambda text, title="收货提醒", say_times=1: (sent.append((text, title)), {"say": True})[1])
    assert post_json("/api/remind/test", {}) == (200, {"say": True})
    assert sent == [("测试：买东西 App 的提醒能响。", "测试提醒")]


def test_post_unknown_path_is_404():
    assert post_json("/api/nope", {}) == (404, {"error": "not found"})

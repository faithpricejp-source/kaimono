"""让每个用例都跑在自己的临时库上：SHOPPING_DATA 指向 pytest 的 tmp_path 并 reload 模块。

另外默认把「真发提醒」「真联网」的入口换成会报错的桩，避免误触 osascript / say / 邮件 / Claude。
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

SERVER = Path(__file__).resolve().parent.parent / "server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

LOAD_ORDER = ("app", "recommend", "remind")


def boom(*args, **kwargs):
    raise AssertionError("测试里不许真发提醒、真联网")


@pytest.fixture(autouse=True)
def shopping_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SHOPPING_DATA", str(tmp_path))
    mods = {}
    for name in LOAD_ORDER:
        mod = sys.modules.get(name) or importlib.import_module(name)
        mods[name] = importlib.reload(mod)
    mods["app"].init_db()
    monkeypatch.setattr(mods["remind"], "send_alert", boom)
    monkeypatch.setattr(mods["recommend"], "ask_claude", boom)
    monkeypatch.setattr(mods["recommend"], "url_ok", boom)
    # 真实账本在 ~/workspace，测试只读自己的临时 csv（默认不存在）
    monkeypatch.setattr(mods["recommend"], "LEDGER", tmp_path / "no-ledger.csv")
    return mods

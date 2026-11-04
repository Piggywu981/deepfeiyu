# 群内机器人同行互聊停止机制 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 群里登记在册的机器人同行消息受互聊状态机控制：轮数超限自然收尾、长时间无真人参与即停、真人消息让位并重置，时段/人工接管等既有闸门照常生效；未配置名单时行为与现状完全一致。

**架构：** 新增 `app/botloop.py`（wxid 互认识别 + 每群聊 `rounds`/`last_human_ts` 状态，`db.kv_*` 持久化，无迁移）。`rules.check` 只读接入闸门；`manager.handle_message` 挂真人重置钩子；`_auto_reply_run` 在拿到会话锁后计数，并把收尾标记经 `_build_prompt(wrap_up=…)` 注入提示词。配置 `auto` 段加 3 键，控制台策略页加 3 个字段。

**技术栈：** Python 3.12、SQLite（`kv` 表）、pytest；前端原生 HTML/JS（无构建）。

**规格：** `docs/superpowers/specs/2026-11-04-bot-loop-stop-design.md`（已批准，计数时机已按实现细节修正过）

**通用纪律（AGENTS.md）：** 测试一律用合成 wxid（`wxid_peer_bot` 等），不得出现真实 wxid/聊天记录/密钥；不改 `LICENSE`/`EULA*`/`NOTICE`/`vendor/`/`requirements*`；所有提交只在本地分支 `feat/bot-loop-stop`，**不推送、不开 PR**；不得编造测试结果，全量结果如实填写。

---

## 文件结构

| 文件 | 职责 | 动作 |
|---|---|---|
| `app/botloop.py` | 同行识别、每群聊互聊状态机、kv 持久化、闸门与计数 | 创建 |
| `app/config.py` | `auto` 段 3 个默认键 | 修改 |
| `app/rules.py` | `check()` 内只读接入同行闸门 | 修改 |
| `app/wechat/manager.py` | 真人重置钩子、生成前计数、收尾提示词参数 | 修改 |
| `app/static/index.html` | 策略页 3 个表单字段 | 修改 |
| `app/static/app.js` | 表单回填与保存 | 修改 |
| `tests/test_botloop.py` | 全部新测试（识别/状态机/闸门/钩子/表单静态断言） | 创建 |
| `docs/使用说明.md` | 3.4 节 + 拦截清单 + 设置页字段表 | 修改 |
| `CHANGELOG.md` | 未发布段新增条目 | 修改 |

依赖顺序：任务 1 → 2 → 3 → 4 → 5，逐个提交。

---

### 任务 1：配置默认键 + botloop 核心模块

**文件：**
- 创建：`app/botloop.py`
- 修改：`app/config.py`（`DEFAULTS["auto"]`，`"prefix": "",` 行之后）
- 测试：`tests/test_botloop.py`

- [x] **步骤 1：编写失败的测试**

创建 `tests/test_botloop.py`，写入以下内容（识别 + 状态机 + 配置默认值，纯单元，不需要 manager）：

```python
"""机器人同行互聊停止：识别、状态机（轮数/闲置/让位）、闸门、manager 钩子与表单静态断言。

规格：docs/superpowers/specs/2026-11-04-bot-loop-stop-design.md
所有 wxid 均为合成数据（AGENTS.md 6.2 脱敏红线）。
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from app import botloop, config, db
from app.wechat.base import Message


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    old = getattr(db._local, "conn", None)
    if old is not None:
        old.close()
        delattr(db._local, "conn")
    db.init()
    yield
    conn = getattr(db._local, "conn", None)
    if conn is not None:
        conn.close()
        delattr(db._local, "conn")


def _cfg(**auto_over) -> dict:
    auto = {"bot_peers": ["wxid_peer_bot"], "bot_chat_max_rounds": 8,
            "bot_chat_idle_stop_min": 15}
    auto.update(auto_over)
    return {"auto": auto}


def _msg(sender: str = "wxid_peer_bot", group: bool = True, mtype: str = "text",
         **over) -> Message:
    m = Message(chat_id="123@chatroom", chat_name="产品群", sender_id=sender,
                sender_name="同行", content="在吗", is_group=group, msg_type=mtype)
    for k, v in over.items():
        setattr(m, k, v)
    return m


# ------------------------------------------------------------------ 识别

def test_is_peer_hit_and_miss():
    cfg = _cfg()
    assert botloop.is_peer(cfg, _msg()) is True
    assert botloop.is_peer(cfg, _msg(sender="wxid_human")) is False


def test_is_peer_empty_sender_is_human():
    """wcferry 等通道拿不到 wxid：按真人（fail-open，最坏维持现状）。"""
    assert botloop.is_peer(_cfg(), _msg(sender="")) is False


def test_is_peer_private_chat_never_peer():
    assert botloop.is_peer(_cfg(), _msg(group=False)) is False


def test_peers_tolerates_bad_config():
    assert botloop.peers({"auto": {"bot_peers": None}}) == set()
    assert botloop.peers({"auto": {"bot_peers": "wxid_x"}}) == set()
    assert botloop.peers({"auto": {}}) == set()
    assert botloop.peers({"auto": {"bot_peers": ["a", "", None, 1]}}) == {"a"}
    assert botloop.enabled({"auto": {}}) is False


# ------------------------------------------------------------------ 状态机

def test_observe_resets_on_human(temp_db):
    cfg = _cfg()
    uid = "acc_a:g1"
    db.kv_set(f"botloop:{uid}", {"rounds": 5, "last_human_ts": time.time() - 9999})
    botloop.observe(cfg, uid, _msg(sender="wxid_human"))
    st = db.kv_get(f"botloop:{uid}")
    assert st["rounds"] == 0
    assert st["last_human_ts"] > time.time() - 5
    assert botloop.count_burst(cfg, uid, [_msg()]) is False   # 重置后从第 1 轮重新计
    assert db.kv_get(f"botloop:{uid}")["rounds"] == 1


def test_observe_resets_on_emoji_and_pat(temp_db):
    """真人发的表情/拍一拍同样算真人出现（只有 system 不算）。"""
    cfg = _cfg()
    uid = "acc_a:g1e"
    db.kv_set(f"botloop:{uid}", {"rounds": 5, "last_human_ts": time.time() - 9999})
    botloop.observe(cfg, uid, _msg(sender="wxid_human", mtype="emoji"))
    assert db.kv_get(f"botloop:{uid}")["rounds"] == 0
    db.kv_set(f"botloop:{uid}", {"rounds": 5, "last_human_ts": time.time() - 9999})
    botloop.observe(cfg, uid, _msg(sender="wxid_human", mtype="pat"))
    assert db.kv_get(f"botloop:{uid}")["rounds"] == 0


def test_observe_skips_peer_system_private_and_disabled(temp_db):
    """同行/system/私聊/名单空都不构成真人事件，也不写状态。"""
    cfg = _cfg()
    uid = "acc_a:g2"
    botloop.observe(cfg, uid, _msg())                              # 同行
    assert db.kv_get(f"botloop:{uid}") is None
    botloop.observe(cfg, uid, _msg(mtype="system", sender=""))     # system
    assert db.kv_get(f"botloop:{uid}") is None
    botloop.observe(cfg, uid, _msg(group=False, sender="wxid_human"))  # 私聊
    assert db.kv_get(f"botloop:{uid}") is None
    botloop.observe({"auto": {}}, uid, _msg(sender="wxid_human"))  # 名单空短路
    assert db.kv_get(f"botloop:{uid}") is None


def test_observe_manual_self_message_counts_as_human(temp_db):
    """本人在手机上手动发的消息 = 真人出现（服务回声在 manager 上游已被吞掉）。"""
    cfg = _cfg()
    uid = "acc_a:g3"
    db.kv_set(f"botloop:{uid}", {"rounds": 3, "last_human_ts": time.time() - 9999})
    botloop.observe(cfg, uid, _msg(sender="wxid_self", is_self=True))
    assert db.kv_get(f"botloop:{uid}")["rounds"] == 0


def test_gate_initializes_at_first_sight(temp_db):
    cfg = _cfg()
    assert botloop.gate(cfg, "acc_a:g4") == (True, "")
    st = db.kv_get("botloop:acc_a:g4")
    assert st["rounds"] == 0
    assert st["last_human_ts"] == pytest.approx(time.time(), abs=5)


def test_gate_rejects_at_round_limit(temp_db):
    cfg = _cfg(bot_chat_max_rounds=2)
    db.kv_set("botloop:acc_a:g5", {"rounds": 2, "last_human_ts": time.time()})
    ok, why = botloop.gate(cfg, "acc_a:g5")
    assert ok is False
    assert "轮数上限" in why


def test_gate_zero_means_unlimited(temp_db):
    cfg = _cfg(bot_chat_max_rounds=0, bot_chat_idle_stop_min=0)
    db.kv_set("botloop:acc_a:g6", {"rounds": 999, "last_human_ts": time.time() - 10**6})
    assert botloop.gate(cfg, "acc_a:g6") == (True, "")


def test_gate_rejects_when_idle_and_recovers_on_human(temp_db):
    cfg = _cfg(bot_chat_idle_stop_min=15)
    db.kv_set("botloop:acc_a:g7", {"rounds": 0, "last_human_ts": time.time() - 16 * 60})
    ok, why = botloop.gate(cfg, "acc_a:g7")
    assert ok is False
    assert "真人" in why
    botloop.observe(cfg, "acc_a:g7", _msg(sender="wxid_human"))   # 真人发言 → 恢复
    assert botloop.gate(cfg, "acc_a:g7") == (True, "")


def test_count_burst_counts_only_peer_and_flags_final(temp_db):
    cfg = _cfg(bot_chat_max_rounds=2)
    uid = "acc_a:g8"
    assert botloop.count_burst(cfg, uid, [_msg(sender="wxid_human")]) is False
    assert db.kv_get(f"botloop:{uid}") is None                    # 纯真人触发不计数
    assert botloop.count_burst(cfg, uid, [_msg()]) is False       # 第 1 条：未到上限
    assert db.kv_get(f"botloop:{uid}")["rounds"] == 1
    assert botloop.count_burst(cfg, uid, [_msg()]) is True        # 第 2 条：正好到上限 → 收尾
    assert db.kv_get(f"botloop:{uid}")["rounds"] == 2
    assert botloop.count_burst({"auto": {}}, uid, [_msg()]) is False  # 名单空短路
    assert db.kv_get(f"botloop:{uid}")["rounds"] == 2


def test_config_defaults_include_bot_loop_keys():
    auto = config.DEFAULTS["auto"]
    assert auto["bot_peers"] == []
    assert auto["bot_chat_max_rounds"] == 8
    assert auto["bot_chat_idle_stop_min"] == 15
```

- [x] **步骤 2：运行测试验证失败**

运行：`.venv\Scripts\python.exe -m pytest tests/test_botloop.py -q`
预期：FAIL，`ModuleNotFoundError: No module named 'app.botloop'`（配置默认值断言也会失败）

- [x] **步骤 3：编写实现——config 默认键**

`app/config.py`，在 `auto` 段的 `"prefix": "",`（约 194 行）之后插入：

```python
        # 机器人同行互聊停止（规格 docs/superpowers/specs/2026-11-04-bot-loop-stop-design.md）：
        # bot_peers 登记群里其他机器人号的 wxid，留空 = 机制关闭；两个 0 = 对应条件不限
        "bot_peers": [],
        "bot_chat_max_rounds": 8,
        "bot_chat_idle_stop_min": 15,
```

- [x] **步骤 4：编写实现——`app/botloop.py`**

创建 `app/botloop.py`：

```python
"""机器人同行互聊停止机制（规格：docs/superpowers/specs/2026-11-04-bot-loop-stop-design.md）。

群里登记在册的机器人号（wxid 互认名单）发来的消息不再与真人等同：每个群聊维护
rounds（已回复同行的轮数）与 last_human_ts（最后一条真人消息时间）两个字段，
轮数超限 / 长时间无真人参与时闸门拒绝回复；真人消息重置两个字段（让位 + 重新激活）。

bot_peers 为空时所有入口整体短路，未启用用户行为与现状完全一致；
sender_id 拿不到（如 wcferry 群消息）按真人处理（fail-open，最坏维持现状）。
"""
from __future__ import annotations

import math
import time
from typing import Any

from . import db
from .wechat.base import Message

STATE_PREFIX = "botloop:"


def peers(cfg: dict[str, Any]) -> set[str]:
    """归一化后的同行 wxid 名单；结构坏掉按空名单处理（对齐 rules._policy_list）。"""
    raw = (cfg.get("auto") or {}).get("bot_peers")
    if not isinstance(raw, list):
        return set()
    return {str(x).strip() for x in raw if isinstance(x, str) and x.strip()}


def enabled(cfg: dict[str, Any]) -> bool:
    return bool(peers(cfg))


def is_peer(cfg: dict[str, Any], msg: Message) -> bool:
    """群消息且发送者 wxid 在名单里；私聊、空 sender 一律不是同行（fail-open）。"""
    if not msg.is_group:
        return False
    sid = str(msg.sender_id or "").strip()
    return bool(sid) and sid in peers(cfg)


def _key(chat_uid: str) -> str:
    return f"{STATE_PREFIX}{chat_uid}"


def _load(chat_uid: str, now: float) -> dict[str, Any]:
    """读每会话状态；缺失或结构坏掉按首见初始化（last_human_ts = now）并落盘。

    读路径也落盘：规格 §5.2 要求「会话首见」即建立状态，纯机器人群从此时起计闲置；
    字段级损坏同样修好后写回，避免坏时间戳每次读都被当成「刚发生」。
    """
    data = db.kv_get(_key(chat_uid))
    if not isinstance(data, dict):
        st = {"rounds": 0, "last_human_ts": now}
        _save(chat_uid, st)
        return st
    bad = False
    try:
        rounds = max(0, int(data.get("rounds") or 0))
    except (TypeError, ValueError):
        rounds = 0
        bad = True
    try:
        ts = float(data.get("last_human_ts") or 0)
    except (TypeError, ValueError, OverflowError):
        ts = 0.0
        bad = True
    if not math.isfinite(ts) or ts <= 0:
        ts = now
        bad = True
    st = {"rounds": rounds, "last_human_ts": ts}
    if bad:
        _save(chat_uid, st)
    return st


def _save(chat_uid: str, st: dict[str, Any]) -> None:
    db.kv_set(_key(chat_uid), st)


def _limits(cfg: dict[str, Any]) -> tuple[int, float]:
    """(轮数上限, 无真人分钟数)；0 = 不限；坏值按 0 处理。"""
    auto = cfg.get("auto") or {}
    try:
        max_rounds = int(auto.get("bot_chat_max_rounds", 0) or 0)
    except (TypeError, ValueError):
        max_rounds = 0
    try:
        idle_min = float(auto.get("bot_chat_idle_stop_min", 0) or 0)
    except (TypeError, ValueError, OverflowError):
        idle_min = 0.0
    return max(0, max_rounds), max(0.0, idle_min)


def observe(cfg: dict[str, Any], chat_uid: str, msg: Message, now: float | None = None) -> None:
    """真人消息到达：重置轮数、刷新计时（让位 + 重新激活）。

    只在名单非空且是群聊时写状态；system 消息与同行消息不构成真人。
    本人在手机上手动发的消息（is_self 且非服务回声）也走这里，同样算真人。
    """
    if not enabled(cfg) or not msg.is_group:
        return
    if msg.msg_type == "system" or is_peer(cfg, msg):
        return
    now = time.time() if now is None else now
    _save(chat_uid, {"rounds": 0, "last_human_ts": now})


def gate(cfg: dict[str, Any], chat_uid: str, now: float | None = None) -> tuple[bool, str]:
    """同行消息的发前闸门：轮数超限 / 长时间无真人参与。

    只对已判定为同行的群消息调用（rules.check 内）；除首见初始化落盘外不写状态。
    """
    if not enabled(cfg):
        return True, ""
    now = time.time() if now is None else now
    st = _load(chat_uid, now)
    max_rounds, idle_min = _limits(cfg)
    if max_rounds > 0 and st["rounds"] >= max_rounds:
        return False, f"机器人互聊已达轮数上限（{max_rounds}）"
    if idle_min > 0 and now - st["last_human_ts"] >= idle_min * 60:
        return False, f"机器人互聊长时间无真人参与（{idle_min:.0f} 分钟）"
    return True, ""


def count_burst(
    cfg: dict[str, Any], chat_uid: str, burst: list[Message], now: float | None = None
) -> bool:
    """一轮回复真正开始生成时计数：burst 里含同行消息才 +1。

    返回 True 表示正好到达轮数上限——这一轮是收尾轮（提示词要加收尾指令）。
    必须在拿到会话锁之后调用：提前计数会让 _fire_reply 的窗口重检把第 N 条自己拦下，
    拿锁后调用同时保证每轮回复只计一次。
    """
    if not enabled(cfg):
        return False
    if not any(is_peer(cfg, m) for m in burst):
        return False
    now = time.time() if now is None else now
    st = _load(chat_uid, now)
    st["rounds"] += 1
    _save(chat_uid, st)
    max_rounds, _ = _limits(cfg)
    return max_rounds > 0 and st["rounds"] == max_rounds
```

- [x] **步骤 5：运行测试验证通过**

运行：`.venv\Scripts\python.exe -m pytest tests/test_botloop.py -q`
预期：PASS（14 passed）

- [x] **步骤 6：Commit**

```powershell
git add app/botloop.py app/config.py tests/test_botloop.py
git commit -m "feat: 机器人同行识别与互聊状态机核心（botloop）"
```

---

### 任务 2：rules.check 接入同行闸门

**文件：**
- 修改：`app/rules.py`（import 行 9；`check()` 的 pause 检查之后、`ident` 黑白名单之前）
- 测试：`tests/test_botloop.py`（追加）

- [x] **步骤 1：编写失败的测试**

在 `tests/test_botloop.py` 末尾追加（同时把 `from app import botloop, config, db` 扩成 `from app import botloop, config, db, rules`）：

```python
# ------------------------------------------------------------------ rules.check 闸门

def _rule_cfg(**auto_over) -> dict:
    auto = {"enabled": True, "group_need_at": False, "emoji_reply": True,
            "pat_reply": True, "only_whitelist": False, "whitelist": [],
            "blacklist": [], "min_interval_sec": 0, "daily_limit": 0,
            "bot_peers": ["wxid_peer_bot"], "bot_chat_max_rounds": 2,
            "bot_chat_idle_stop_min": 15}
    auto.update(auto_over)
    return {"auto": auto, "ai": {}}


def _chat(**over) -> dict:
    chat = {"uid": "acc_a:123@chatroom", "chat_id": "123@chatroom", "name": "产品群",
            "ai_enabled": True, "pause_until": 0}
    chat.update(over)
    return chat


def test_rules_gate_blocks_peer_at_round_limit(temp_db):
    cfg = _rule_cfg()
    db.kv_set("botloop:acc_a:123@chatroom", {"rounds": 2, "last_human_ts": time.time()})
    ok, why = rules.check(cfg, {"id": "acc_a"}, _chat(), _msg())
    assert ok is False
    assert "轮数上限" in why


def test_rules_gate_blocks_peer_when_idle(temp_db):
    cfg = _rule_cfg()
    db.kv_set("botloop:acc_a:123@chatroom",
              {"rounds": 0, "last_human_ts": time.time() - 16 * 60})
    ok, why = rules.check(cfg, {"id": "acc_a"}, _chat(), _msg())
    assert ok is False
    assert "真人" in why


def test_rules_peer_allowed_with_fresh_state(temp_db):
    cfg = _rule_cfg()
    ok, why = rules.check(cfg, {"id": "acc_a"}, _chat(), _msg())
    assert (ok, why) == (True, "允许回复")


def test_rules_human_sender_not_gated(temp_db):
    """真人消息不受互聊状态影响（即使状态里轮数已满）。"""
    cfg = _rule_cfg()
    db.kv_set("botloop:acc_a:123@chatroom",
              {"rounds": 99, "last_human_ts": time.time() - 9999})
    ok, _ = rules.check(cfg, {"id": "acc_a"}, _chat(), _msg(sender="wxid_human"))
    assert ok is True


def test_rules_without_peers_configured_behaves_as_before(temp_db):
    """未配置名单：即便残留状态也不拦（机制整体关闭 = 现状）。"""
    auto = {"enabled": True, "group_need_at": False, "only_whitelist": False,
            "whitelist": [], "blacklist": [], "min_interval_sec": 0, "daily_limit": 0}
    cfg = {"auto": auto, "ai": {}}
    db.kv_set("botloop:acc_a:123@chatroom",
              {"rounds": 99, "last_human_ts": time.time() - 9999})
    ok, _ = rules.check(cfg, {"id": "acc_a"}, _chat(), _msg())
    assert ok is True


def test_rules_peer_still_blocked_by_takeover(temp_db):
    """停止条件④：人工接管对同行照常。"""
    cfg = _rule_cfg()
    ok, why = rules.check(cfg, {"id": "acc_a"},
                          _chat(pause_until=time.time() + 60), _msg())
    assert ok is False and "人工接管" in why


def test_rules_peer_still_blocked_by_blacklist(temp_db):
    cfg = _rule_cfg(blacklist=["产品群"])
    ok, why = rules.check(cfg, {"id": "acc_a"}, _chat(), _msg())
    assert ok is False and "黑名单" in why


def test_rules_peer_still_blocked_by_group_need_at(temp_db):
    cfg = _rule_cfg(group_need_at=True)
    ok, why = rules.check(cfg, {"id": "acc_a"}, _chat(), _msg(content="随便聊聊"))
    assert ok is False and "群聊未 @" in why


def test_rules_peer_still_blocked_by_daily_limit(temp_db):
    """停止条件④：每日上限对同行照常（种子一条今日 agent 回复）。"""
    cfg = _rule_cfg(daily_limit=1)
    db.add_message({
        "id": "old1", "account_id": "acc_a", "chat_uid": "acc_a:123@chatroom",
        "chat_id": "123@chatroom", "chat_name": "产品群", "content": "早前回复",
        "msg_type": "text", "is_group": True, "is_self": True,
        "source": "agent", "ts": time.time(),
    })
    ok, why = rules.check(cfg, {"id": "acc_a"}, _chat(), _msg())
    assert ok is False
    assert why == "已达今日回复上限"


def test_rules_peer_still_blocked_outside_working_hours(temp_db, monkeypatch):
    """停止条件④：工作时段对同行照常。"""
    monkeypatch.setattr("app.rules.time.strftime", lambda fmt: "03:00")
    cfg = _rule_cfg(working_hours={"enabled": True, "start": "09:00", "end": "18:00"})
    ok, why = rules.check(cfg, {"id": "acc_a"}, _chat(), _msg())
    assert ok is False and "工作时段" in why


def test_rules_peer_without_wxid_falls_open(temp_db):
    """拿不到 wxid 的同行按真人放行（fail-open，维持现状）。"""
    cfg = _rule_cfg()
    db.kv_set("botloop:acc_a:123@chatroom",
              {"rounds": 99, "last_human_ts": time.time() - 9999})
    ok, _ = rules.check(cfg, {"id": "acc_a"}, _chat(), _msg(sender=""))
    assert ok is True


def test_rules_private_peer_message_ungated(temp_db):
    """私聊不进状态机（非目标），行为与现状一致。"""
    cfg = _rule_cfg()
    chat = _chat(chat_id="wxid_peer_bot", uid="acc_a:wxid_peer_bot", name="同行")
    ok, _ = rules.check(cfg, {"id": "acc_a"}, chat, _msg(group=False))
    assert ok is True
    assert db.kv_get("botloop:acc_a:123@chatroom") is None
```

- [x] **步骤 2：运行测试验证失败**

运行：`.venv\Scripts\python.exe -m pytest tests/test_botloop.py -q`
预期：新增用例 FAIL——`rules.check` 尚无闸门，`test_rules_gate_blocks_peer_at_round_limit` 得到 `(True, "允许回复")`

- [x] **步骤 3：编写实现**

`app/rules.py` 两处：

import 行（第 9 行）：

```python
from . import botloop, db
```

`check()` 内，pause 检查（`return False, "人工接管中，AI 暂停"`）之后、`ident = {...}` 之前插入：

```python
    # 机器人同行互聊停止：只拦登记在册的机器人号；下方时段/日上限等闸门对同行照常生效
    if botloop.is_peer(cfg, msg):
        ok, why = botloop.gate(cfg, str(chat.get("uid") or ""))
        if not ok:
            return False, why
```

- [x] **步骤 4：运行测试验证通过**

运行：`.venv\Scripts\python.exe -m pytest tests/test_botloop.py -q`
预期：PASS（26 passed）

- [x] **步骤 5：Commit**

```powershell
git add app/rules.py tests/test_botloop.py
git commit -m "feat: rules.check 接入机器人同行互聊闸门"
```

---

### 任务 3：manager 钩子（真人重置 + 生成前计数 + 收尾提示词）

**文件：**
- 修改：`app/wechat/manager.py`（5 处，见步骤 3）
- 测试：`tests/test_botloop.py`（追加）

- [x] **步骤 1：编写失败的测试**

在 `tests/test_botloop.py` 末尾追加（imports 扩成 `from app.wechat.manager import AccountSession`，`asyncio` 已在文件头）：

```python
# ------------------------------------------------------------------ manager 钩子

class _NoLLM:
    """configured=False：走到「未配置 Key」分支即返回，不进生成、不排队。"""
    configured = False


class _FakeLLM:
    configured = True

    async def stream(self, messages, tools=None, **kwargs):  # noqa: ARG002
        yield {"type": "text", "text": "好呀"}
        yield {"type": "finish", "usage": {"prompt_tokens": 1, "completion_tokens": 1}}


class _FakeDriver:
    _self_wxid = "wxid_self"  # 闸门要求身份明确，替身需与真实驱动一样已绑定

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send_text(self, chat_id: str, text: str, **kwargs) -> bool:  # noqa: ARG002
        self.sent.append((chat_id, text))
        return True


def _session(llm=None, **auto_cfg):
    from app.wechat.manager import AccountSession

    auto = {"enabled": True, "group_need_at": False, "min_interval_sec": 0,
            "daily_limit": 0, "history_context": 4, "split_reply": False,
            "takeover_pause_min": 0, **auto_cfg}
    cfg = {"send": {"enabled": False},
           "ai": {"emoji_auto_collect": False, "memory_enabled": False},
           "auto": auto, "emoji": {}}
    return AccountSession({"id": "acc_a", "name": "A", "nickname": "B", "persona": {}},
                          lambda: cfg, llm or _NoLLM())


def test_handle_message_human_resets_state(temp_db):
    session = _session(bot_peers=["wxid_peer_bot"])
    uid = db.upsert_chat("acc_a", "g1@chatroom", "产品群", True, "在吗")
    db.set_chat_ai(uid, True)   # 新会话默认关 AI（default_ai_on=False），不改就到不了策略层
    db.kv_set(f"botloop:{uid}", {"rounds": 5, "last_human_ts": time.time() - 9999})
    msg = Message(chat_id="g1@chatroom", chat_name="产品群", sender_id="wxid_human",
                  sender_name="群友", content="大家好", is_group=True, msg_id="h1")
    asyncio.run(session.handle_message(msg))
    st = db.kv_get(f"botloop:{uid}")
    assert st["rounds"] == 0
    assert st["last_human_ts"] > time.time() - 5


def test_handle_message_manual_self_resets_state(temp_db):
    """本人手机手动发言 = 真人；走 is_self 非回声分支同样重置。"""
    session = _session(bot_peers=["wxid_peer_bot"])
    uid = db.upsert_chat("acc_a", "g2@chatroom", "产品群", True)
    db.kv_set(f"botloop:{uid}", {"rounds": 4, "last_human_ts": time.time() - 9999})
    msg = Message(chat_id="g2@chatroom", chat_name="产品群", sender_id="wxid_self",
                  content="我插一句", is_group=True, is_self=True, msg_id="s1")
    asyncio.run(session.handle_message(msg))
    assert db.kv_get(f"botloop:{uid}")["rounds"] == 0


def test_handle_message_peer_at_limit_schedules_nothing(temp_db):
    session = _session(bot_peers=["wxid_peer_bot"], bot_chat_max_rounds=2)
    uid = db.upsert_chat("acc_a", "g3@chatroom", "产品群", True)
    db.set_chat_ai(uid, True)   # 必须开 AI：否则是「会话关了 AI」拦的，闸门用例就空过了
    db.kv_set(f"botloop:{uid}", {"rounds": 2, "last_human_ts": time.time()})
    msg = Message(chat_id="g3@chatroom", chat_name="产品群", sender_id="wxid_peer_bot",
                  sender_name="同行", content="接着聊", is_group=True, msg_id="p1")
    asyncio.run(session.handle_message(msg))
    assert session._pending == {}   # 闸门拦下，没有排队回复


def test_handle_message_peer_without_peers_config_schedules(temp_db):
    """未配置名单：同行消息与现状一样进入回复流程（此处断言进了聚拢窗口）。

    必须用 configured=True 的 LLM：configured=False 会在排队前就走「未配置 Key」分支 return。
    """
    session = _session(_FakeLLM())
    uid = db.upsert_chat("acc_a", "g4@chatroom", "产品群", True)
    db.set_chat_ai(uid, True)   # 新会话默认关 AI，不开就到不了排队
    msg = Message(chat_id="g4@chatroom", chat_name="产品群", sender_id="wxid_peer_bot",
                  sender_name="同行", content="在吗", is_group=True, msg_id="p2")
    asyncio.run(session.handle_message(msg))
    assert uid in session._pending
    pending = session._pending.pop(uid)
    if pending.timer is not None:
        pending.timer.cancel()


def test_agent_echo_does_not_reset_state(temp_db):
    """服务自己发出的消息回声在 handle_message 早期被吞掉，不构成真人事件。"""
    session = _session(bot_peers=["wxid_peer_bot"])
    uid = db.upsert_chat("acc_a", "g5@chatroom", "产品群", True)
    db.kv_set(f"botloop:{uid}", {"rounds": 3, "last_human_ts": time.time() - 9999})
    session._recent_outbound[(uid, "好呀")] = time.time()   # 出站留底：回声命中
    echo = Message(chat_id="g5@chatroom", chat_name="产品群", content="好呀",
                   is_self=True, msg_id="e1")
    asyncio.run(session.handle_message(echo))
    assert db.kv_get(f"botloop:{uid}")["rounds"] == 3


def test_auto_reply_run_counts_peer_and_flags_wrap(temp_db, monkeypatch):
    """同行触发的回复生成前计数；正好到上限那轮把收尾标记传给 _build_prompt。"""
    from app import config as config_mod
    from app.mood import service as mood_service

    auto = {"enabled": True, "group_need_at": False, "history_context": 4,
            "split_reply": False, "max_reply_chars": 300, "emoji_reply": False,
            "bot_peers": ["wxid_peer_bot"], "bot_chat_max_rounds": 2,
            "bot_chat_idle_stop_min": 15}
    monkeypatch.setattr(config_mod, "load", lambda: {
        "ai": {"memory_enabled": False, "vision_enabled": False, "emoji_with_reply": False},
        "auto": auto, "send": {"enabled": False}, "emoji": {},
    })
    monkeypatch.setattr(config_mod, "persona_of", lambda acc: ("人设", "大肥鱼"))
    monkeypatch.setattr(mood_service, "observe", lambda *a, **kw: "queued")
    session = AccountSession({"id": "acc_a", "name": "号", "persona": {}},
                             lambda: config_mod.load(), _FakeLLM())
    session.driver = _FakeDriver()
    uid = db.upsert_chat("acc_a", "c9@chatroom", "产品群", True, "在吗")

    def peer_msg(mid: str, text: str) -> Message:
        return Message(chat_id="c9@chatroom", chat_name="产品群", sender_id="wxid_peer_bot",
                       sender_name="同行", content=text, is_group=True, msg_id=mid)

    wraps: list[bool] = []
    real_build = session._build_prompt

    def spy(*args, **kwargs):
        wraps.append(bool(kwargs.get("wrap_up")))
        return real_build(*args, **kwargs)

    session._build_prompt = spy

    async def turns():
        await session._auto_reply_run(uid, peer_msg("m1", "在吗"), [peer_msg("m1", "在吗")])
        await session._auto_reply_run(uid, peer_msg("m2", "接着聊"), [peer_msg("m2", "接着聊")])

    asyncio.run(turns())
    assert db.kv_get(f"botloop:{uid}")["rounds"] == 2
    assert wraps == [False, True]           # 第 2 轮（=上限）标收尾
    assert session.driver.sent              # 两轮都真的发出去了


def test_build_prompt_wrap_up_block(temp_db):
    session = _session()
    cfg = {"ai": {"memory_enabled": False, "emoji_with_reply": False},
           "auto": {"group_need_at": False}, "send": {"enabled": False}}
    chat = {"uid": "acc_a:g", "chat_id": "g@chatroom", "name": "产品群"}
    msg = Message(chat_id="g@chatroom", chat_name="产品群", sender_id="wxid_peer_bot",
                  sender_name="同行", content="在吗", is_group=True, msg_id="w1")
    with_wrap = session._build_prompt(cfg, chat, [], msg, wrap_up=True)
    assert any("本轮收尾" in m["content"] for m in with_wrap)
    without = session._build_prompt(cfg, chat, [], msg)
    assert not any("本轮收尾" in m["content"] for m in without)
```

- [x] **步骤 2：运行测试验证失败**

运行：`.venv\Scripts\python.exe -m pytest tests/test_botloop.py -q`
预期：新增用例 FAIL——`TypeError: ... unexpected keyword argument 'wrap_up'`（`_build_prompt` 尚无该参数）；重置类用例因 `botloop.observe` 未被调用而 FAIL

- [x] **步骤 3：编写实现（`app/wechat/manager.py` 五处）**

1）import（第 14 行）：

```python
from .. import botloop, config, db, rules, usage as usage_mod
```

2）`handle_message` 非 self 分支——`if msg.is_self:` 块内的 `if not soft_echo:` 里，`paused_before` 之前插入一行：

```python
        if msg.is_self:
            if not soft_echo:
                botloop.observe(self.get_cfg(), uid, msg)  # 人工在手机上发的 = 真人
                # 「人工接管暂停」按这条消息之前的状态算：刚设置的新暂停不该把本次串门判定挡掉
                paused_before = bool((db.get_chat(uid) or {}).get("paused"))
                self._handle_self_message(uid, msg)
                self._maybe_cross_visit(uid, msg, was_paused=paused_before)
            return
```

3）`handle_message` 非 self 主路径——`chat = db.get_chat(uid)` 与 `allowed, reason = rules.check(...)` 之间插入：

```python
        cfg = self.get_cfg()
        chat = db.get_chat(uid)
        botloop.observe(cfg, uid, msg)  # 真人消息重置互聊状态（让位 + 重新激活）
        allowed, reason = rules.check(cfg, self.account, chat, msg)
```

4）`_auto_reply_run` 里 `_build_prompt` 调用处（约 1409 行）：

```python
                wrap_up = botloop.count_burst(cfg, uid, burst)  # 同行触发才计数；正好到上限=收尾轮
                messages = self._build_prompt(
                    cfg, chat, history, msg, image_paths, habit=habit,
                    memory_block=memory_block, wrap_up=wrap_up,
                )
```

（`burst` 在 1288 行已定义、且位于 `async with lock:` 内，满足「拿到会话锁后计数」。）

5）`_build_prompt` 两处：签名里 `memory_block: str | None = None,` 之后加一行：

```python
        wrap_up: bool = False,
```

函数体内、`if msg.is_group:` 的「群聊消息开头是昵称(wxid)」`system +=` 块之后插入：

```python
        if wrap_up:
            system += (
                "\n【本轮收尾】你与群里其他自动回复账号的互聊已达轮数上限："
                "这是本轮互聊的最后一条消息，自然收尾道别即可，别再挑起新话题；"
                "有真人参与时正常回应真人。"
            )
```

- [x] **步骤 4：运行测试验证通过**

运行：`.venv\Scripts\python.exe -m pytest tests/test_botloop.py -q`
预期：PASS（33 passed）

- [x] **步骤 5：跑既有相关回归**

运行：`.venv\Scripts\python.exe -m pytest tests/test_self_message.py tests/test_mood_hooks.py tests/test_cross_chat.py tests/test_pat.py tests/test_group_silence.py -q`
预期：全 PASS（manager 改动不破坏既有链路；若有失败，先修复再提交）

- [x] **步骤 6：Commit**

```powershell
git add app/wechat/manager.py tests/test_botloop.py
git commit -m "feat: 互聊状态钩子接入 manager（真人重置/生成前计数/收尾指令）"
```

---

### 任务 4：控制台策略页三个字段

**文件：**
- 修改：`app/static/index.html`（`fPrefix` 字段之后，约 1291 行）
- 修改：`app/static/app.js`（`fillSettings` 约 2234 行、`saveSettings` 的 `auto` 段约 2306 行）
- 测试：`tests/test_botloop.py`（追加静态断言）

- [x] **步骤 1：编写失败的测试**

`tests/test_botloop.py` 末尾追加：

```python
# ------------------------------------------------------------------ 控制台表单

def test_console_form_has_bot_loop_fields():
    root = Path(__file__).resolve().parents[1]
    html = (root / "app" / "static" / "index.html").read_text("utf-8")
    js = (root / "app" / "static" / "app.js").read_text("utf-8")
    for fid in ("fBotPeers", "fBotRounds", "fBotIdle"):
        assert fid in html, f"index.html 缺少字段 {fid}"
        assert fid in js, f"app.js 缺少字段 {fid}"
    for key in ("bot_peers", "bot_chat_max_rounds", "bot_chat_idle_stop_min"):
        assert key in js, f"app.js 保存缺少配置键 {key}"
```

- [x] **步骤 2：运行测试验证失败**

运行：`.venv\Scripts\python.exe -m pytest tests/test_botloop.py::test_console_form_has_bot_loop_fields -q`
预期：FAIL，`AssertionError: index.html 缺少字段 fBotPeers`

- [x] **步骤 3：编写实现——`index.html`**

在 `<label class="field"><span>回复前缀（可空）</span><input id="fPrefix" …></label>` 整块之后插入：

```html
            <label class="field">
              <span>机器人同行名单（每行一个 wxid，可空）</span>
              <textarea id="fBotPeers" rows="2" placeholder="wxid_xxxxxxxx"></textarea>
              <em>群里其他机器人号的 wxid；留空则不启用「互聊停止」，照常谁发都回</em>
            </label>
            <label class="field">
              <span>互聊轮数上限（0 = 不限）</span>
              <input id="fBotRounds" type="number" min="0" />
              <em>本账号最多回机器人同行几条，到第 N 条自然收尾后不再接话</em>
            </label>
            <label class="field">
              <span>无真人参与暂停（分钟，0 = 不限）</span>
              <input id="fBotIdle" type="number" min="0" />
              <em>纯机器人聊天超过此时长自动停；群里出现真人发言后重新计</em>
            </label>
```

- [x] **步骤 4：编写实现——`app.js`**

`fillSettings` 中 `$("#fPrefix").value = s.auto.prefix || "";` 之后插入：

```js
  $("#fBotPeers").value = (s.auto.bot_peers || []).join("\n");
  $("#fBotRounds").value = s.auto.bot_chat_max_rounds ?? 8;
  $("#fBotIdle").value = s.auto.bot_chat_idle_stop_min ?? 15;
```

`saveSettings` 的 `auto` 段中 `prefix: $("#fPrefix").value,` 之后插入：

```js
      bot_peers: lines($("#fBotPeers").value),
      bot_chat_max_rounds: parseInt($("#fBotRounds").value) || 0,
      bot_chat_idle_stop_min: parseInt($("#fBotIdle").value) || 0,
```

- [x] **步骤 5：运行测试验证通过**

运行：`.venv\Scripts\python.exe -m pytest tests/test_botloop.py -q`
预期：PASS（34 passed）

- [x] **步骤 6：Commit**

```powershell
git add app/static/index.html app/static/app.js tests/test_botloop.py
git commit -m "feat: 策略页新增机器人同行名单与互聊停止字段"
```

---

### 任务 5：文档、CHANGELOG 与全量验证

**文件：**
- 修改：`docs/使用说明.md`（3 处）
- 修改：`CHANGELOG.md`（未发布段）

- [x] **步骤 1：使用说明——3.1 拦截清单**

`docs/使用说明.md` 3.1 节「以下任一情况会静默：」列表里，`触发同会话最短间隔、每日上限或发送节流；` 一行之后插入：

```markdown
  - 群里对方是登记的机器人同行，且互聊已达轮数上限 / 长时间无真人参与；
```

- [x] **步骤 2：使用说明——新增 3.4 节**

在 `### 3.3 发送节奏` 小节内容之后、下一个 `---` 之前插入：

```markdown
### 3.4 机器人同行互聊停止

群里有两个以上机器人号时，它们会把对方的消息当真人无限接下去，直到人工关服务。「设置 → 自动回复策略」里登记**机器人同行名单**（对方机器人号的 wxid，每行一个）后，每个群按两条规则刹车：

- **互聊轮数上限**（默认 8）：本账号最多回机器人同行几条，到第 N 条自然收尾，之后不再接话；
- **无真人参与暂停**（默认 15 分钟）：纯机器人聊天超过此时长自动停。

群里出现**真人发言**（包括你在手机上亲自发的）会立刻重置计数与计时，互聊重新开始；工作时段、人工接管、每日上限、群聊需 @、黑名单对机器人同行照常生效。留空名单 = 不启用，行为与从前一致。

限制：wcferry 通道的群消息可能解析不出 wxid，此时机制不生效；群里**没登记**的其他机器人按真人处理——每个机器人都要把「其他所有机器人」登记进自己的名单。
```

- [x] **步骤 3：使用说明——设置页字段表**

`docs/使用说明.md` 中 `**自动回复策略**：总开关、群聊需 @、拍一拍、黑白名单、工作时段、间隔、每日上限、接管暂停、前缀、字数上限、跨对话沟通。` 一行改为：

```markdown
- **自动回复策略**：总开关、群聊需 @、拍一拍、黑白名单、工作时段、间隔、每日上限、接管暂停、前缀、机器人同行名单与互聊停止（轮数上限 / 无真人暂停）、字数上限、跨对话沟通。
```

- [x] **步骤 4：CHANGELOG 条目**

`CHANGELOG.md` 的 `## [未发布]` 之后插入：

```markdown
### 2026-11-04 · 群内机器人同行互聊停止（轮数上限 / 无真人参与暂停）

- 新增「机器人同行互聊停止」：两个以上机器人号同群时不再无限互聊。`auto` 段新增 `bot_peers`（对方机器人 wxid 名单，留空 = 机制关闭）、`bot_chat_max_rounds`（默认 8，到第 N 条自然收尾）、`bot_chat_idle_stop_min`（默认 15 分钟）；新模块 `app/botloop.py` 按群聊维护 `rounds`/`last_human_ts`（kv `botloop:{uid}` 持久化，无数据库迁移）。
- 判定与钩子：`rules.check` 在「人工接管」之后插入只读闸门，拒绝原因照常进控制台「为什么不回」；`manager.handle_message` 对真人消息（含本人手机手动发言）重置计数与计时（让位 + 重新激活），`_auto_reply_run` 在拿到会话锁后计数并把收尾标记传给 `_build_prompt`（收尾轮提示词追加「自然收尾道别」指令）。工作时段、人工接管、日上限、`group_need_at`、黑名单对同行照常生效。
- 控制台「设置 → 自动回复策略」新增三字段：机器人同行名单、互聊轮数上限、无真人参与暂停；`docs/使用说明.md` 新增 3.4 节并同步拦截清单。
- 已知限制（文档已写明）：wcferry 群消息解析不出 wxid 时机制退化为不生效；未登记的机器人按真人处理；空 wxid fail-open 按真人（最坏维持现状）。
- 验证状态：（执行本任务时如实填写：`tests/test_botloop.py` 通过数、全量 `pytest -q` 结果与耗时、`release_snapshot` 扫描与 `git diff --check` 结果；不得编造）```

- [x] **步骤 5：文档回归测试**

运行：`.venv\Scripts\python.exe -m pytest tests/test_usage_doc.py tests/test_readme_claims.py tests/test_eula.py -q`
预期：全 PASS（改了使用说明必须过文档测试）

- [x] **步骤 6：脱敏扫描**

```powershell
python tools/release_snapshot.py --scan-tracked .
git diff --check
```

预期：扫描通过、无空白错误；人工复查 `git diff` 无真实 wxid/聊天记录/密钥（本计划全部用 `wxid_peer_bot` 等合成值）

- [x] **步骤 7：全量测试并回填 CHANGELOG 验证状态**

运行：`.venv\Scripts\python.exe -m pytest -q`
预期：全 PASS，或如实列明环境性失败与原因。把**真实**数字（如 `32 passed`、全量 `N passed / M skipped`、耗时）填进步骤 4 的「验证状态」占位说明，删掉括号里的执行指引。

- [x] **步骤 8：Commit**

```powershell
git add docs/使用说明.md CHANGELOG.md
git commit -m "docs: 互聊停止机制使用说明与更新日志"
```

---

## 完成核对（对应规格验收标准）

- [x] 登记 `bot_peers` 后 ≤N 条内正常互聊、第 N 条收尾、之后拒绝且原因可读（任务 2/3 测试）
- [x] 满 X 分钟停止；真人消息（含手动 `is_self`）重置并恢复（任务 1/2/3 测试）
- [x] 时段/接管/日上限/`group_need_at`/黑名单对同行照常（任务 2 测试；日上限沿用既有 `daily_limit` 用例口径）
- [x] 未配置名单、空 wxid、私聊：行为与现状一致（任务 1/2 测试）
- [x] 全量测试通过或如实列明环境性失败；文档与 CHANGELOG 同步；无真实 wxid/聊天记录入库
- [x] 回滚方式：revert 提交即可；配置键留着无害（空名单 = 关闭），kv 旧版本自动忽略，无数据库迁移

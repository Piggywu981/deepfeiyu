# 桌宠与心情双端融合 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 让微信控制台与 DeepSeek Harness 共享同一份心情状态、同一只桌宠，并在 DSH 设置页与挂饰里读写它。

**架构：** 控制台的 `data/agent.db` 是唯一状态源；新增 `app/dsh_bridge/` 暴露 `/api/dsh/*`，DSH 插件 Host 在 Harness 回合结束时空跑判断并把结果写回，同时把工具/回合事件转成宠物事件；DSH Client 只做展示与控制。

**技术栈：** Python 3.12 + FastAPI + SQLite（既有）、原生 JS（控制台）、Cordis bundle Host/Client JS（DSH 插件，无构建）、pytest、node --check。

**规格：** `docs/superpowers/specs/2026-11-02-pet-mood-fusion-design.md`

---

## 文件结构

### 控制台（Python）

| 文件 | 职责 |
|---|---|
| `app/config.py` | 修改：`DEFAULTS` 增加 `dsh` 段（绑定账号与三个开关） |
| `app/dsh_bridge/__init__.py` | 新建：空文件 |
| `app/dsh_bridge/service.py` | 新建：绑定解析、状态组装、参数校验、限流、宠物事件发布 |
| `app/dsh_bridge/api.py` | 新建：6 个 `/api/dsh/*` 端点 + 本机来源校验 |
| `app/main.py` | 修改：注册 `dsh_router`（2 行） |
| `app/mood/service.py` | 修改：抽出 `apply_judged_delta()`；`inject_block` 增加跨端来历分支；`state_payload` 增加 `lastSource` |
| `app/mood/store.py` | 修改：新增 `last_event()` 只读查询（复用 `list_events`，不改表结构） |
| `app/pet/reducer.py` | 修改：接受 `origin` / `title` 并透传到消息 |
| `app/pet/bubble.py` | 修改：`decorate` / `message_for` 支持 `source_label` |
| `app/pet/supervisor.py` | 修改：`_decorate` 按消息来源拼气泡标签 |
| `tests/test_dsh_bridge.py` | 新建：桥接端点、规则共用、宠物事件、隐私规则 |
| `tests/test_pet_bubble.py` | 修改：补来源标签用例 |

### DSH 插件（JS，无构建）

| 文件 | 职责 |
|---|---|
| `dsh-plugin/lib/fusion.js` | 新建：会话文本缓存、判断、限流、宠物事件上报、提示词上下文 |
| `dsh-plugin/lib/ops.js` | 修改：新增 `dshState` / `dshConfig` / `dshTimeline` / `dshJudgePrompt` / `dshMoodEvent` / `dshPetEvent` |
| `dsh-plugin/lib/route.js` | 修改：新增 `fusion.state` / `fusion.config` / `fusion.timeline` |
| `dsh-plugin/index.js` | 修改：创建 fusion 并挂 disposer |
| `dsh-plugin/client.js` | 修改：设置页「心情与桌宠（融合）」+ `shell.overlay` 挂饰 |
| `dsh-plugin/README.md` | 修改：说明融合能力、开关、隐私边界 |
| `tests/test_dsh_plugin.py` | 修改：融合路由/监听/挂饰静态断言 |

### 文档

| 文件 | 职责 |
|---|---|
| `README.md` | 修改：DSH 章节补融合说明与开关表 |
| `docs/使用说明.md` | 修改：DSH 设置页与心情/桌宠关系 |
| `CHANGELOG.md` | 修改：新增 2026-11-02 条目 |

---

## 任务 1：控制台配置段 + 桥接骨架 + 状态/配置端点

**文件：**
- 修改：`app/config.py:144`（`pet` 段之后）
- 创建：`app/dsh_bridge/__init__.py`、`app/dsh_bridge/service.py`、`app/dsh_bridge/api.py`
- 修改：`app/main.py:31`（import）、`app/main.py:177`（include_router 附近）
- 测试：`tests/test_dsh_bridge.py`

- [ ] **步骤 1：编写失败的测试**

```python
"""DSH 融合桥接：绑定、状态、共享心情写入、宠物事件与隐私规则。"""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import app.main as main
from app import config, db
from app.dsh_bridge import api as bridge_api
from app.dsh_bridge import service as bridge_service

ACCOUNT = {"id": "acc_a", "name": "工作号", "driver": "webhook", "enabled": True}


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


@pytest.fixture()
def cfg(monkeypatch):
    """真实 config 模块的 load/save/accounts 全部换成内存字典。"""
    mood_account = {
        "inject_mode": "event", "inject_confidence": 60, "inject_per_day": 6,
        "base": 70, "floor": 0, "decay_half_hours": 12,
        "sig_mood": 3, "sig_dim": 5, "judge_model": "",
    }
    state = {
        "mood": {"enabled": True, "accounts": {"acc_a": mood_account}},
        "dsh": {"account_id": "", "judge_enabled": True,
                "pet_events_enabled": True, "cross_reason_enabled": True},
    }

    def load():
        return {"active_account": "acc_a", "accounts": [dict(ACCOUNT)], **state}

    def save(patch):
        for key, value in patch.items():
            if isinstance(value, dict) and isinstance(state.get(key), dict):
                state[key] = {**state[key], **value}
            else:
                state[key] = value
        return load()

    monkeypatch.setattr(config, "load", load)
    monkeypatch.setattr(config, "save", save)
    monkeypatch.setattr(config, "accounts", lambda: [dict(ACCOUNT)])
    monkeypatch.setattr(config, "account",
                        lambda aid: dict(ACCOUNT) if aid == "acc_a" else None)
    monkeypatch.setattr(config, "active_id", lambda: "acc_a")
    monkeypatch.setattr(config, "persona_of", lambda account: ("p", "大肥鱼"))
    return state


def _client() -> TestClient:
    return TestClient(main.app)


def test_state_unbound_falls_back_to_active(cfg):
    cfg["dsh"]["account_id"] = "gone"
    body = _client().get("/api/dsh/state").json()
    assert body["ok"] is True
    assert body["accountId"] == "acc_a"
    assert body["boundFallback"] is True
    assert body["config"]["judge_enabled"] is True
    assert [a["id"] for a in body["accounts"]] == ["acc_a"]


def test_config_updates_binding_and_switches(cfg):
    res = _client().post("/api/dsh/config", json={
        "account_id": "acc_a", "cross_reason_enabled": False,
    })
    assert res.status_code == 200
    body = res.json()
    assert body["accountId"] == "acc_a" and body["boundFallback"] is False
    assert body["config"]["cross_reason_enabled"] is False
    assert cfg["dsh"]["cross_reason_enabled"] is False


def test_config_rejects_unknown_account(cfg):
    res = _client().post("/api/dsh/config", json={"account_id": "nope"})
    assert res.status_code == 400
    assert "账号" in res.json()["detail"]


def test_remote_host_rejected():
    class _Remote:
        client = type("C", (), {"host": "8.8.8.8"})()

    with pytest.raises(HTTPException) as caught:
        bridge_api._require_local(_Remote())
    assert caught.value.status_code == 403
```

- [ ] **步骤 2：运行测试验证失败**

运行：`.venv\Scripts\python.exe -m pytest tests/test_dsh_bridge.py -q`
预期：FAIL（`ModuleNotFoundError: No module named 'app.dsh_bridge'`）

- [ ] **步骤 3：实现配置段**

`app/config.py` 在 `"pet": {...}` 段之后加：

```python
    # DSH 融合：绑定一个微信账号，共享心情与桌宠；编辑入口在 DSH 设置页。
    "dsh": {
        "account_id": "",
        "judge_enabled": True,
        "pet_events_enabled": True,
        "cross_reason_enabled": True,
    },
```

- [ ] **步骤 4：实现 `app/dsh_bridge/service.py`（本任务只做绑定/状态/配置）**

```python
"""DSH 融合桥接：绑定账号、共享心情读写、宠物事件。

状态源永远在控制台（data/agent.db / data/config.json）；这里只做参数校验与转发，
不保存任何业务状态，避免两套数据漂移。
"""
from __future__ import annotations

from typing import Any

from .. import config
from ..mood import service as mood_service
from ..mood import settings as mood_settings
from ..mood import store as mood_store
from ..pet.supervisor import supervisor as pet_supervisor

DEFAULTS: dict[str, Any] = {
    "account_id": "",
    "judge_enabled": True,
    "pet_events_enabled": True,
    "cross_reason_enabled": True,
}
_BOOL_KEYS = ("judge_enabled", "pet_events_enabled", "cross_reason_enabled")


def section() -> dict[str, Any]:
    raw = config.load().get("dsh")
    merged = dict(DEFAULTS)
    if isinstance(raw, dict):
        merged.update({key: raw[key] for key in DEFAULTS if key in raw})
    return merged


def accounts_payload() -> list[dict[str, Any]]:
    active = config.active_id()
    return [
        {"id": account["id"], "name": account.get("name") or account["id"],
         "active": account["id"] == active}
        for account in config.accounts()
    ]


def bound_account() -> tuple[str, bool]:
    """返回 (绑定账号, 是否回落到当前激活账号)。绑定失效时不让接口 500。"""
    wanted = str(section().get("account_id") or "").strip()
    if wanted and config.account(wanted) is not None:
        return wanted, False
    return config.active_id(), bool(wanted)


def timeline_payload(limit: int = 20) -> list[dict[str, Any]]:
    account_id, _ = bound_account()
    slot_id = mood_settings.resolve_slot(account_id, "")
    rows = mood_store.list_events(account_id, slot_id, limit=max(1, min(int(limit), 100)))
    return [
        {**row, "origin": "harness" if row.get("source") == "dsh" else "wechat"}
        for row in rows
    ]


def state_payload() -> dict[str, Any]:
    account_id, fallback = bound_account()
    return {
        "ok": True,
        "accountId": account_id,
        "boundFallback": fallback,
        "accounts": accounts_payload(),
        "config": section(),
        "mood": mood_service.state_payload(account_id),
        "timeline": timeline_payload(),
        "pet": pet_supervisor.status(),
    }


def save_config(patch: dict[str, Any]) -> dict[str, Any]:
    allowed: dict[str, Any] = {}
    if "account_id" in patch:
        aid = str(patch.get("account_id") or "").strip()
        if aid and config.account(aid) is None:
            raise ValueError(f"账号不存在：{aid}")
        allowed["account_id"] = aid
    for key in _BOOL_KEYS:
        if key in patch:
            if not isinstance(patch[key], bool):
                raise ValueError(f"{key} 必须是布尔值")
            allowed[key] = patch[key]
    if allowed:
        config.save({"dsh": allowed})
    return state_payload()
```

- [ ] **步骤 5：实现 `app/dsh_bridge/api.py`**

```python
"""DSH 融合接口：绑定、共享心情、宠物事件、时间线与判断提示词。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import config
from . import service

router = APIRouter(prefix="/api/dsh", tags=["dsh"])


class ConfigBody(BaseModel):
    account_id: str | None = None
    judge_enabled: bool | None = None
    pet_events_enabled: bool | None = None
    cross_reason_enabled: bool | None = None


def _require_local(request: Request) -> None:
    host = request.client.host if request.client else ""
    if host not in config.LOCAL_HOSTS:
        raise HTTPException(403, "DSH 融合接口只允许在本机控制台执行")


@router.get("/state")
def get_state(request: Request) -> dict[str, Any]:
    _require_local(request)
    return service.state_payload()


@router.post("/config")
def post_config(request: Request, body: ConfigBody) -> dict[str, Any]:
    _require_local(request)
    try:
        return service.save_config(body.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
```

- [ ] **步骤 6：注册路由**

`app/main.py` 顶部 import 区（`from .deps.api import router as deps_router` 附近）加：

```python
from .dsh_bridge.api import router as dsh_router
```

`app.include_router(pet_router)` 之后加：

```python
app.include_router(dsh_router)
```

- [ ] **步骤 7：运行测试验证通过**

运行：`.venv\Scripts\python.exe -m pytest tests/test_dsh_bridge.py -q`
预期：4 passed

- [ ] **步骤 8：Commit**

```bash
git add app/config.py app/dsh_bridge app/main.py tests/test_dsh_bridge.py
git commit -m "feat(dsh-bridge): 配置段、绑定解析与状态/配置端点"
```

---

## 任务 2：判断提示词端点 + 共享心情写入

**文件：**
- 修改：`app/mood/store.py`（新增 `last_event`）
- 修改：`app/mood/service.py`（抽出 `apply_judged_delta`）
- 修改：`app/dsh_bridge/service.py`、`app/dsh_bridge/api.py`
- 测试：`tests/test_dsh_bridge.py`

- [ ] **步骤 1：编写失败的测试（追加到 `tests/test_dsh_bridge.py`）**

```python
def _seed_mood(account_id="acc_a", slot_id="default"):
    from app.mood import store
    state = store.load_state(account_id, slot_id)
    state.update({"mood": 70.0, "last_confidence": 80, "updated_at": 1000.0})
    store.save_state(account_id, slot_id, state)


def test_judge_prompt_is_rendered_for_bound_account(cfg):
    body = _client().get("/api/dsh/judge-prompt").json()
    assert body["version"] and len(body["version"]) == 12
    assert "只输出一个 JSON 对象" in body["system"]
    assert "moodDelta" in body["schema"]


def test_mood_event_applies_and_records_dsh_source(temp_db, cfg):
    _seed_mood()
    res = _client().post("/api/dsh/mood-event", json={
        "moodDelta": 6,
        "dims": {"happy": 8, "calm": 0, "sweet": 2,
                 "heartache": 0, "worry": 0, "sad": 0, "jealous": 0},
        "confidence": 85,
        "reason": "用户在 Harness 里把一个麻烦的 bug 交给我，我认真查完了",
    })
    assert res.status_code == 200
    body = res.json()
    assert body["applied"] is True and body["accountId"] == "acc_a"
    from app.mood import store
    events = store.list_events("acc_a", "default", limit=5)
    assert events and events[0]["source"] == "dsh"
    assert len(events[0]["reason"]) <= 60


def test_mood_event_rejects_out_of_range(cfg):
    res = _client().post("/api/dsh/mood-event", json={
        "moodDelta": 99, "dims": {}, "confidence": 80, "reason": "x"})
    assert res.status_code == 400


def test_mood_event_skipped_when_judge_disabled(cfg):
    cfg["dsh"]["judge_enabled"] = False
    body = _client().post("/api/dsh/mood-event", json={
        "moodDelta": 3, "dims": {}, "confidence": 80, "reason": "x"}).json()
    assert body["applied"] is False and body["skipped"] == "judge-disabled"


def test_apply_judged_delta_shared_by_wechat_and_dsh(temp_db, cfg):
    from app.mood import service as mood_service
    from app.mood import store
    _seed_mood()
    result = mood_service.apply_judged_delta(
        "acc_a", "default",
        {"moodDelta": 6,
         "dims": {"happy": 6, "calm": 0, "sweet": 0,
                  "heartache": 0, "worry": 0, "sad": 0, "jealous": 0},
         "confidence": 90, "reason": "r" * 80},
        source="dsh",
    )
    assert result["applied"] is True and result["significant"] is True
    events = store.list_events("acc_a", "default", limit=1)
    assert events[0]["source"] == "dsh"
    assert events[0]["reason"] == "r" * 60          # 跨端理由只在短句内
    assert events[0]["kind"] == "judge" and events[0]["minor"] == 0

- [ ] **步骤 2：运行测试验证失败**

运行：`.venv\Scripts\python.exe -m pytest tests/test_dsh_bridge.py -q`
预期：FAIL（404 / `AttributeError: apply_judged_delta`）

- [ ] **步骤 3：实现 `store.last_event()`**

`app/mood/store.py` 在 `list_events` 之后加：

```python
def last_event(account_id: str, slot_id: str) -> dict[str, Any] | None:
    """最近一条心情事件：跨端注入判断需要它的 source，但不新增数据库列。"""
    rows = list_events(account_id, slot_id, limit=1)
    return rows[0] if rows else None
```

- [ ] **步骤 4：抽出 `app/mood/service.apply_judged_delta()`**

在 `app/mood/service.py` 的 `_run` 前新增：

```python
def apply_judged_delta(
    account_id: str,
    slot_id: str,
    delta: dict[str, Any],
    *,
    source: str = "dsh",
    reason_limit: int = 60,
    chat_uid: str = "",
    st: dict[str, Any] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """把已经判断好的 delta 应用到槽位并落事件。

    微信判断与 DSH 回写共用这一套：显著阈值、minor、注入额度状态、衰减、事件格式都相同；
    唯一区别是 source 与 reason 截断（跨端理由只保留短句）。
    """
    st = st or settings.settings(account_id)
    reason = str(delta.get("reason") or "")[: max(1, int(reason_limit))]
    delta = {**delta, "reason": reason}
    step = model.mood_step(delta["moodDelta"])
    any_dim = any(delta["dims"][key] != 0 for key in DIM_KEYS)
    if step == 0 and not any_dim:
        return {"applied": False, "skipped": "no-change"}
    with state_lock(account_id, slot_id):
        state = store.load_state(account_id, slot_id, base=st["base"])
        _decay_note(account_id, slot_id, state, st, time.time())
        sig = model.significant(delta, st["sig_mood"], st["sig_dim"])
        at = time.time() if now is None else float(now)
        model.apply_delta(state, delta, st["floor"], at)
        state["minor"] = 0 if sig else 1
        state["last_chat_uid"] = chat_uid
        store.save_state_with_event(
            account_id, slot_id, state,
            event={
                "at": at,
                "mood": state["mood"],
                "delta": model.mood_step(delta["moodDelta"]),
                "dims": state["dims"],
                "dims_delta": delta["dims"],
                "reason": reason,
                "confidence": delta["confidence"],
                "kind": "judge",
                "source": source,
                "minor": state["minor"],
            },
        )
    return {
        "applied": True,
        "skipped": "",
        "mood": state["mood"],
        "dims": state["dims"],
        "confidence": state["last_confidence"],
        "minor": state["minor"],
        "significant": sig,
        "event": {"at": at, "source": source, "reason": reason},
    }
```

然后把 `_run` 里“判断之后”的整段替换为：

```python
            result = apply_judged_delta(
                account_id, slot_id, delta, source=source, chat_uid=chat_uid, st=st,
            )
            if not result["applied"]:
                log.info("这一轮判定无变化，不记入时间线（%s）", source)
                return
            log.info("心情更新（%s）：mood=%.1f minor=%s reason=%s",
                     source, result["mood"], result["minor"], result["event"]["reason"][:80])
```

删除 `_run` 里重复的 `state_lock` / `store.save_state_with_event` 块。

- [ ] **步骤 5：实现桥接的心情写入与提示词**

`app/dsh_bridge/service.py` 顶部补 import：

```python
import hashlib

from ..mood import prompt as mood_prompt
from ..mood.constants import DIM_KEYS
```

在文件末尾加：

```python
def _normalize_delta(body: dict[str, Any]) -> dict[str, Any]:
    try:
        mood_delta = float(body.get("moodDelta"))
    except (TypeError, ValueError) as exc:
        raise ValueError("moodDelta 必须是数字") from exc
    if not -10.0 <= mood_delta <= 10.0:
        raise ValueError("moodDelta 超出 [-10, 10]")
    raw_dims = body.get("dims") if isinstance(body.get("dims"), dict) else {}
    dims: dict[str, float] = {}
    for key in DIM_KEYS:
        try:
            value = float(raw_dims.get(key) or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"dims.{key} 必须是数字") from exc
        if not -10.0 <= value <= 10.0:
            raise ValueError(f"dims.{key} 超出 [-10, 10]")
        dims[key] = value
    try:
        confidence = int(body.get("confidence"))
    except (TypeError, ValueError) as exc:
        raise ValueError("confidence 必须是整数") from exc
    if not 0 <= confidence <= 100:
        raise ValueError("confidence 超出 [0, 100]")
    reason = "".join(
        ch for ch in str(body.get("reason") or "") if ch.isprintable()
    ).strip()[:60]
    return {"moodDelta": mood_delta, "dims": dims,
            "confidence": confidence, "reason": reason}


def judge_prompt_payload() -> dict[str, Any]:
    account_id, _ = bound_account()
    slot_id = mood_settings.resolve_slot(account_id, "")
    self_name, owner, aliases = mood_settings.names(account_id, slot_id, chat_name="")
    system = mood_prompt.system(self_name, owner, aliases)
    schema = (
        '{"moodDelta":0,"dims":{"happy":0,"calm":0,"sweet":0,"heartache":0,'
        '"worry":0,"sad":0,"jealous":0},"reason":"","confidence":80}'
    )
    return {
        "version": hashlib.sha256(system.encode("utf-8")).hexdigest()[:12],
        "system": system,
        "schema": schema,
    }


def apply_mood_event(body: dict[str, Any]) -> dict[str, Any]:
    sec = section()
    if not sec["judge_enabled"]:
        return {"applied": False, "skipped": "judge-disabled"}
    account_id, _ = bound_account()
    if not mood_service.settings.enabled(account_id):
        return {"applied": False, "skipped": "mood-disabled"}
    slot_id = mood_settings.resolve_slot(account_id, "")
    delta = _normalize_delta(body)
    result = mood_service.apply_judged_delta(
        account_id, slot_id, delta, source="dsh", chat_uid="", reason_limit=60,
    )
    return {**result, "accountId": account_id, "mood": mood_service.state_payload(account_id)}
```

> `mood_service.settings` 是 `app.mood.service` 里已 import 的 `settings` 模块（见该文件头部），直接用即可；若没有，改成 `from ..mood import settings as mood_settings` 后调用 `mood_settings.enabled(account_id)`。

- [ ] **步骤 6：补 API 端点**

`app/dsh_bridge/api.py` 末尾加：

```python
@router.get("/judge-prompt")
def get_judge_prompt(request: Request) -> dict[str, Any]:
    _require_local(request)
    return service.judge_prompt_payload()


@router.post("/mood-event")
def post_mood_event(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    _require_local(request)
    try:
        return service.apply_mood_event(body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/timeline")
def get_timeline(request: Request, limit: int = 20) -> dict[str, Any]:
    _require_local(request)
    return {"ok": True, "events": service.timeline_payload(limit)}
```

- [ ] **步骤 7：运行测试验证通过**

运行：`.venv\Scripts\python.exe -m pytest tests/test_dsh_bridge.py tests/test_mood_inject.py tests/test_mood_store.py tests/test_mood_api.py -q`
预期：全部 PASS（既有心情测试不回归）

- [ ] **步骤 8：Commit**

```bash
git add app/mood/store.py app/mood/service.py app/dsh_bridge tests/test_dsh_bridge.py
git commit -m "feat(dsh-bridge): 共享心情写入与判断提示词端点"
```

---

## 任务 3：跨端来历注入规则

**文件：**
- 修改：`app/mood/service.py`（`inject_block`、`state_payload`）
- 测试：`tests/test_dsh_bridge.py`

- [ ] **步骤 1：编写失败的测试（追加）**

```python
def _apply_dsh(mood_delta=6, reason="用户在 Harness 里把 bug 交给我，我查完了"):
    from app.dsh_bridge import service as svc
    return svc.apply_mood_event({
        "moodDelta": mood_delta,
        "dims": {"happy": 5, "calm": 0, "sweet": 0,
                 "heartache": 0, "worry": 0, "sad": 0, "jealous": 0},
        "confidence": 90, "reason": reason,
    })


def test_dsh_reason_enters_private_wechat_when_enabled(temp_db, cfg):
    from app.mood import service as mood_service
    _seed_mood()
    _apply_dsh(reason="用户在 Harness 里把 bug 交给我，我查完了")
    line = mood_service.inject_block("acc_a", chat_uid="acc_a:wxid_1")
    assert "bug" in line


def test_dsh_reason_downgrades_when_switch_off(temp_db, cfg):
    cfg["dsh"]["cross_reason_enabled"] = False
    from app.mood import service as mood_service
    _seed_mood()
    _apply_dsh()
    assert "bug" not in mood_service.inject_block("acc_a", chat_uid="acc_a:wxid_1")


def test_dsh_reason_downgrades_in_group(temp_db, cfg):
    from app.mood import service as mood_service
    _seed_mood()
    _apply_dsh()
    assert "bug" not in mood_service.inject_block(
        "acc_a", chat_uid="acc_a:group_1", is_group=True)
```

- [ ] **步骤 2：运行测试验证失败**

运行：`.venv\Scripts\python.exe -m pytest tests/test_dsh_bridge.py -q`
预期：FAIL（DSH 理由进不了私聊；群聊没有断言失败也可能 PASS，需先确认私聊用例失败）

- [ ] **步骤 3：实现注入分支**

`app/mood/service.py` 顶部 import 区补一行（当前文件没有导入 config）：

```python
from .. import config
```

把 `inject_block` 里这段：

```python
            mode = st["inject_mode"]
            if mode == "event" and state.get("kind") != "decay":
                other_chat = bool(
                    chat_uid
                    and state.get("last_chat_uid")
                    and chat_uid != state.get("last_chat_uid")
                )
                if is_group or other_chat:
                    mode = "label"
```

替换为：

```python
            mode = st["inject_mode"]
            if mode == "event" and state.get("kind") != "decay":
                last = store.last_event(account_id, slot_id) or {}
                is_dsh = last.get("source") == "dsh"
                cross_dsh = is_dsh and bool(
                    (config.load().get("dsh") or {}).get("cross_reason_enabled", True)
                )
                other_chat = bool(
                    chat_uid
                    and state.get("last_chat_uid")
                    and chat_uid != state.get("last_chat_uid")
                )
                if is_group:
                    mode = "label"          # 群聊永不带来历，跨端也一样
                elif is_dsh:
                    mode = "event" if cross_dsh else "label"
                elif other_chat:
                    mode = "label"
```

- [ ] **步骤 4：`state_payload` 增加 `lastSource`**

在 `state_payload` 的返回字典里加：

```python
        "lastSource": (store.last_event(account_id, slot_id) or {}).get("source", ""),
```

- [ ] **步骤 5：运行测试验证通过**

运行：`.venv\Scripts\python.exe -m pytest tests/test_dsh_bridge.py tests/test_mood_inject.py -q`
预期：PASS

- [ ] **步骤 6：Commit**

```bash
git add app/mood/service.py tests/test_dsh_bridge.py
git commit -m "feat(mood): DSH 来历按开关与场景进入微信注入"
```

---

## 任务 4：宠物事件桥接与来源标注

**文件：**
- 修改：`app/dsh_bridge/service.py`、`app/dsh_bridge/api.py`
- 修改：`app/pet/reducer.py`、`app/pet/bubble.py`、`app/pet/supervisor.py`
- 测试：`tests/test_dsh_bridge.py`、`tests/test_pet_bubble.py`

- [ ] **步骤 1：编写失败的测试（追加）**

```python
def test_pet_event_publishes_bus_event(cfg, monkeypatch):
    from app import bus
    from app.dsh_bridge import service as bridge_service
    from app.pet import supervisor as pet_supervisor
    monkeypatch.setattr(bridge_service, "_pet_last", {})   # 限流是模块级状态，测试间要隔离
    monkeypatch.setattr(pet_supervisor, "status",
                        lambda: {"enabled": True, "running": True})
    published: list[dict] = []
    monkeypatch.setattr(bus.bus, "publish", lambda event: published.append(event))
    body = _client().post("/api/dsh/pet-event", json={
        "kind": "tool_start", "tool": "read",
        "session": {"id": "s1", "title": "修复登录崩溃"},
    }).json()
    assert body["accepted"] is True
    assert published[0]["type"] == "agent_tool"
    assert published[0]["data"]["origin"] == "dsh"
    assert published[0]["data"]["title"] == "修复登录崩溃"


def test_pet_event_rejected_when_pet_off(cfg, monkeypatch):
    from app.dsh_bridge import service as bridge_service
    from app.pet import supervisor as pet_supervisor
    monkeypatch.setattr(bridge_service, "_pet_last", {})
    monkeypatch.setattr(pet_supervisor, "status",
                        lambda: {"enabled": True, "running": False})
    body = _client().post("/api/dsh/pet-event", json={"kind": "turn_start"}).json()
    assert body["accepted"] is False


def test_pet_event_throttled(cfg, monkeypatch):
    from app import bus
    from app.dsh_bridge import service as bridge_service
    from app.pet import supervisor as pet_supervisor
    monkeypatch.setattr(bridge_service, "_pet_last", {})
    monkeypatch.setattr(pet_supervisor, "status",
                        lambda: {"enabled": True, "running": True})
    published: list[dict] = []
    monkeypatch.setattr(bus.bus, "publish", lambda event: published.append(event))
    for _ in range(2):
        _client().post("/api/dsh/pet-event", json={"kind": "tool_start", "tool": "read"})
    assert len(published) == 1
```

`tests/test_pet_bubble.py` 追加：

```python
def test_source_label_rendered_for_harness():
    _, detail = bubble.decorate("WORKING", mood=None, pet_cfg=_cfg(),
                                source_label="Harness · 修复登录崩溃")
    assert "Harness" in detail and "修复登录崩溃" in detail
```

- [ ] **步骤 2：运行测试验证失败**

运行：`.venv\Scripts\python.exe -m pytest tests/test_dsh_bridge.py tests/test_pet_bubble.py -q`
预期：FAIL（404 / `decorate() got an unexpected keyword argument 'source_label'`）

- [ ] **步骤 3：实现桥接宠物事件**

`app/dsh_bridge/service.py` 顶部补：

```python
import time

from .. import bus
```

文件末尾加：

```python
_PET_MIN_GAP_S = 1.0
_pet_last: dict[tuple[str, str], float] = {}


def _pet_bus_event(kind: str, title: str, tool: str) -> dict[str, Any] | None:
    base = {"account_id": config.active_id(), "scope": "auto",
            "origin": "dsh", "title": title[:40]}
    if kind == "turn_start":
        return {"type": "agent_turn", "data": {**base, "type": "start"}}
    if kind == "tool_start":
        return {"type": "agent_tool", "data": {**base, "type": "tool_start",
                                               "name": tool[:40]}}
    if kind in ("tool_done", "turn_end"):
        return {"type": "agent_tool", "data": {**base, "type": "done"}}
    if kind == "error":
        return {"type": "log", "data": {**base, "level": "error"}}
    return None


def publish_pet_event(body: dict[str, Any]) -> dict[str, Any]:
    if not section()["pet_events_enabled"]:
        return {"accepted": False, "reason": "pet-events-disabled"}
    status = pet_supervisor.status()
    if not (status.get("enabled") and status.get("running")):
        return {"accepted": False, "reason": "pet-not-running"}
    kind = str(body.get("kind") or "")
    session = body.get("session") if isinstance(body.get("session"), dict) else {}
    title = str(session.get("title") or "")
    tool = str(body.get("tool") or "")
    event = _pet_bus_event(kind, title, tool)
    if event is None:
        return {"accepted": False, "reason": "unknown-kind"}
    key = (event["data"]["account_id"], kind)
    now = time.time()
    if now - _pet_last.get(key, 0.0) < _PET_MIN_GAP_S:
        return {"accepted": False, "reason": "throttled"}
    _pet_last[key] = now
    bus.bus.publish(event)
    return {"accepted": True}
```

`api.py` 末尾加：

```python
@router.post("/pet-event")
def post_pet_event(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    _require_local(request)
    return service.publish_pet_event(body)
```

- [ ] **步骤 4：宠物 reducer 透传来源**

`app/pet/reducer.py`：

1）`__init__` 里补两个字段：

```python
        self._origin = ""             # 事件来源：wechat 事件为空，DSH 事件为 "dsh"
        self._title = ""              # DSH 会话标题，用于气泡标签
```

2）`on_event` 开头（`data_account` 过滤之后）记录本次事件的来源：

```python
        self._origin = str(data.get("origin") or "")
        self._title = str(data.get("title") or "")[:40]
```

3）`_set` 里构造 message 后追加来源字段：

```python
        message: dict[str, Any] = {"kind": "state", "state": state}
        if activity:
            message["activity"] = activity
        if self._origin:
            message["origin"] = self._origin
        if self._title:
            message["title"] = self._title
```

4）`_finish` 返回 pulse 时同样带上：

```python
        message = {
            "kind": "pulse", "state": "SUCCESS", "ttlMs": PULSE_TTL,
            "resumeState": "IDLE", "resumeActivity": None,
        }
        if self._origin:
            message["origin"] = self._origin
        if self._title:
            message["title"] = self._title
        return [message]
```

- [ ] **步骤 5：气泡支持来源标签**

`app/pet/bubble.py`：

```python
def decorate(state: str, *, mood: dict[str, Any] | None, pet_cfg: dict[str, Any],
             chat_name: str = "", digest: str = "",
             source_label: str = "") -> tuple[str, str]:
    ...
    parts = [detail]
    if source_label:
        parts.append(_clean(source_label, CHAT_NAME_MAX))
    if pet_cfg.get("showChatName") and chat_name:
        parts.append(_clean(chat_name, CHAT_NAME_MAX))
    ...
```

`message_for` 增加 `source_label: str = ""` 参数并透传。

- [ ] **步骤 6：supervisor 拼接标签**

`app/pet/supervisor.py` 的 `_decorate` 中，在调用 `bubble.message_for` 前计算：

```python
        origin = str(message.get("origin") or "")
        title = str(message.get("title") or "")
        source_label = ""
        chat_name = ""
        if origin == "dsh":
            source_label = "Harness" + (f" · {title}" if title else "")
        else:
            chat_name = self._chat_name() if (
                cfg.get("showChatName") and state not in {"IDLE", "DISCONNECTED"}
            ) else ""
```

两个 `bubble.message_for(...)` 调用都传 `source_label=source_label`，并把 `chat_name=chat_name` 改成上面算好的变量。

- [ ] **步骤 7：运行测试验证通过**

运行：`.venv\Scripts\python.exe -m pytest tests/test_dsh_bridge.py tests/test_pet_bubble.py tests/test_pet_reducer.py tests/test_pet_supervisor.py -q`
预期：新增用例 PASS；`test_pet_supervisor.py` 若因本机缺 PySide6 失败，按基线已知环境失败记录（与本次改动无关）。

- [ ] **步骤 8：Commit**

```bash
git add app/dsh_bridge app/pet tests/test_dsh_bridge.py tests/test_pet_bubble.py
git commit -m "feat(pet): 接受 DSH 任务事件并在气泡标注 Harness 来源"
```

---

## 任务 5：DSH Host 采集与宠物事件上报

**文件：**
- 创建：`dsh-plugin/lib/fusion.js`
- 修改：`dsh-plugin/lib/ops.js`、`dsh-plugin/lib/route.js`、`dsh-plugin/index.js`
- 测试：`tests/test_dsh_plugin.py`

- [ ] **步骤 1：编写失败的静态测试（追加到 `tests/test_dsh_plugin.py`）**

```python
def test_fusion_module_collects_events_and_posts_pet_events():
    source = (PKG / "lib" / "fusion.js").read_text(encoding="utf-8")
    for token in ("session/event", "user/message", "assistant/message",
                  "tool/call", "tool/result", "turn/end",
                  "dshPetEvent", "turn_start", "tool_start", "postPet", "dispose"):
        assert token in source, f"lib/fusion.js 缺少 {token}"


def test_fusion_routes_registered():
    route = (PKG / "lib" / "route.js").read_text(encoding="utf-8")
    ops = (PKG / "lib" / "ops.js").read_text(encoding="utf-8")
    for endpoint in ("fusion.state", "fusion.config", "fusion.timeline"):
        assert f"'{endpoint}'" in route, f"route.js 缺少 {endpoint}"
    for path in ("/api/dsh/state", "/api/dsh/config", "/api/dsh/timeline",
                 "/api/dsh/judge-prompt", "/api/dsh/mood-event", "/api/dsh/pet-event"):
        assert path in ops, f"ops.js 缺少 {path}"


def test_index_wires_fusion_with_disposer():
    source = (PKG / "index.js").read_text(encoding="utf-8")
    assert "createFusion" in source and "fusion.dispose" in source
```

- [ ] **步骤 2：运行测试验证失败**

运行：`.venv\Scripts\python.exe -m pytest tests/test_dsh_plugin.py -q`
预期：FAIL（`FileNotFoundError: lib/fusion.js`）

- [ ] **步骤 3：实现 `dsh-plugin/lib/ops.js` 方法**

在 `createConsoleApi` 返回对象里 `capabilities` 之后加：

```js
    // DSH 融合：绑定/共享心情/宠物事件全部走控制台；判断提示词也由控制台渲染。
    dshState: () => call('/api/dsh/state'),
    dshConfig: (patch) => call('/api/dsh/config', { method: 'POST', body: patch, timeoutMs: 15000 }),
    dshTimeline: (limit = 20) => call(`/api/dsh/timeline?limit=${Math.max(1, Math.min(100, Math.trunc(Number(limit) || 20)))}`),
    dshJudgePrompt: () => call('/api/dsh/judge-prompt', { timeoutMs: 15000 }),
    dshMoodEvent: (payload) => call('/api/dsh/mood-event', { method: 'POST', body: payload, timeoutMs: 20000 }),
    dshPetEvent: (payload) => call('/api/dsh/pet-event', { method: 'POST', body: payload, timeoutMs: 5000 }),
```

- [ ] **步骤 4：实现 `dsh-plugin/lib/fusion.js`（采集/宠物事件部分）**

```js
// DSH 侧融合：把 Harness 会话事件变成共享心情判断与桌宠事件。
//
// 硬约束：
// - 只把「判断结果 + 短理由」发给控制台，Harness 原文不出 Host；
// - 控制台不可达时丢弃本轮，不阻塞 Harness；
// - 所有监听/定时器必须在 dispose() 里清理，HMR 重载不能重复注册。
const TURN_TEXT_LIMIT = 2000
const SESSION_CACHE_LIMIT = 32
const PET_DEBOUNCE_MS = 3000

function textOf(message) {
  const blocks = message && Array.isArray(message.content) ? message.content : []
  return blocks
    .filter((block) => block && block.type === 'text' && typeof block.text === 'string')
    .map((block) => block.text)
    .join('\n')
    .trim()
    .slice(0, TURN_TEXT_LIMIT)
}

function sessionTitle(session) {
  const header = session && session.header && typeof session.header === 'object' ? session.header : {}
  const title = header.title || header.name || ''
  return String(title).slice(0, 40)
}

export function createFusion({ ctx, config, api }) {
  const sessions = new Map()
  const disposers = []
  let disposed = false
  let lastPet = ''
  let lastPetAt = 0

  function cache(session, patch) {
    const id = session && session.id ? String(session.id) : ''
    if (!id) return
    const current = sessions.get(id) || { user: '', reply: '', title: '' }
    Object.assign(current, patch)
    sessions.delete(id)
    sessions.set(id, current)
    while (sessions.size > SESSION_CACHE_LIMIT) {
      sessions.delete(sessions.keys().next().value)
    }
  }

  function postPet(kind, extra = {}) {
    if (disposed) return
    const now = Date.now()
    if (kind === lastPet && now - lastPetAt < PET_DEBOUNCE_MS) return
    lastPet = kind
    lastPetAt = now
    Promise.resolve()
      .then(() => api.dshPetEvent({ kind, ...extra }))
      .catch(() => {})
  }

  disposers.push(ctx.on('session/event', (session, event) => {
    if (!event || typeof event.type !== 'string') return
    const sessionInfo = { id: session && session.id ? String(session.id) : '', title: sessionTitle(session) }
    if (event.type === 'user/message') {
      cache(session, { user: textOf(event.data), title: sessionInfo.title })
      postPet('turn_start', { session: sessionInfo })
      return
    }
    if (event.type === 'assistant/message') {
      const reply = textOf(event.data && event.data.message)
      if (reply) cache(session, { reply })
      return
    }
    if (event.type === 'tool/call') {
      postPet('tool_start', { tool: String(event.data && event.data.name || ''), session: sessionInfo })
      return
    }
    if (event.type === 'tool/result') {
      postPet('tool_done', { tool: String(event.data && event.data.name || ''), session: sessionInfo })
      return
    }
    if (event.type === 'turn/end') {
      const reason = event.data && event.data.reason ? event.data.reason : {}
      postPet(reason.kind === 'completed' ? 'turn_end' : 'error', { session: sessionInfo })
    }
  }))

  disposers.push(ctx.on('agent/error', () => {
    postPet('error')
  }))

  function dispose() {
    disposed = true
    for (const disposeFn of disposers.splice(0)) {
      try { if (typeof disposeFn === 'function') disposeFn() } catch {}
    }
  }

  return { dispose }
}
```

- [ ] **步骤 5：路由与 index 接线**

`dsh-plugin/lib/route.js` 的 `routes` 里加：

```js
    // DSH 融合：代理控制台 /api/dsh/*；config 只接受白名单字段。
    'fusion.state': async () => api.dshState(),
    'fusion.config': async (payload) => {
      const patch = {}
      if (typeof payload.account_id === 'string') patch.account_id = payload.account_id
      for (const key of ('judge_enabled', 'pet_events_enabled', 'cross_reason_enabled')) {
        if (typeof payload[key] === 'boolean') patch[key] = payload[key]
      }
      if (!Object.keys(patch).length) {
        throw Object.assign(new Error('没有可更新的字段'), { code: 'INVALID_ARGS' })
      }
      return await api.dshConfig(patch)
    },
    'fusion.timeline': async (payload) => await api.dshTimeline(payload.limit),
```

`dsh-plugin/index.js` 顶部加：

```js
import { createFusion } from './lib/fusion.js'
```

在 `registerRoute(...)` 之后加：

```js
  const fusion = createFusion({ ctx, config, api })
  ctx.effect(() => () => fusion.dispose(), 'deepfeiyu.fusion')
```

- [ ] **步骤 6：运行测试验证通过**

运行：
```powershell
node --check dsh-plugin/lib/fusion.js
node --check dsh-plugin/lib/ops.js
node --check dsh-plugin/lib/route.js
node --check dsh-plugin/index.js
.venv\Scripts\python.exe -m pytest tests/test_dsh_plugin.py -q
```
预期：node 全 0；pytest 全 PASS

- [ ] **步骤 7：Commit**

```bash
git add dsh-plugin/lib/fusion.js dsh-plugin/lib/ops.js dsh-plugin/lib/route.js dsh-plugin/index.js tests/test_dsh_plugin.py
git commit -m "feat(dsh-plugin): Host 采集 Harness 事件并上报宠物事件"
```

---

## 任务 6：DSH Host 心情判断、限流与提示词注入

**文件：**
- 修改：`dsh-plugin/lib/fusion.js`
- 测试：`tests/test_dsh_plugin.py`

- [ ] **步骤 1：编写失败的静态测试（追加）**

```python
def test_fusion_judges_mood_and_injects_prompt_context():
    source = (PKG / "lib" / "fusion.js").read_text(encoding="utf-8")
    for token in ("dshJudgePrompt", "dshMoodEvent", "agentDefaultModel",
                  "streamText(", "systemPrompt", "context(", "deepfeiyu.mood",
                  "JUDGE_MIN_GAP_MS", "JUDGE_DAILY_MAX", "parseJudge"):
        assert token in source, f"lib/fusion.js 缺少 {token}"
```

- [ ] **步骤 2：运行测试验证失败**

运行：`.venv\Scripts\python.exe -m pytest tests/test_dsh_plugin.py -q`
预期：FAIL

- [ ] **步骤 3：实现判断与提示词上下文**

在 `dsh-plugin/lib/fusion.js` 顶部补常量：

```js
const JUDGE_MIN_GAP_MS = 90_000
const JUDGE_DAILY_MAX = 40
const JUDGE_MODEL_MAX_TOKENS = 300
const PROMPT_CACHE_MS = 5 * 60_000
const MOOD_REFRESH_MS = 60_000
```

在 `createFusion` 内、`disposers` 之后补：

```js
  let judgePrompt = null
  let promptAt = 0
  let moodText = ''
  let moodAt = 0
  let judgeAt = 0
  let judgeDay = ''
  let judgeCount = 0
  let judgeBusy = false

  const timer = ctx.get('timer')

  function todayKey() {
    return new Date().toISOString().slice(0, 10)
  }

  async function loadJudgePrompt() {
    if (judgePrompt && Date.now() - promptAt < PROMPT_CACHE_MS) return judgePrompt
    judgePrompt = await api.dshJudgePrompt()
    promptAt = Date.now()
    return judgePrompt
  }

  function parseJudge(text) {
    const start = String(text || '').indexOf('{')
    const end = String(text || '').lastIndexOf('}')
    if (start < 0 || end <= start) return null
    try {
      const data = JSON.parse(String(text).slice(start, end + 1))
      if (!data || typeof data !== 'object') return null
      return data
    } catch {
      return null
    }
  }

  async function streamText(options) {
    let text = ''
    for await (const chunk of ctx.get('llm').stream(options)) {
      if (chunk && chunk.type === 'text-delta' && typeof chunk.text === 'string') text += chunk.text
      if (chunk && chunk.type === 'finish' && chunk.reason && chunk.reason.kind === 'error') return ''
    }
    return text
  }

  async function judgeSession(session) {
    if (judgeBusy || disposed) return
    const id = session && session.id ? String(session.id) : ''
    const cacheItem = sessions.get(id)
    if (!cacheItem || !cacheItem.user || !cacheItem.reply) return
    const day = todayKey()
    if (day !== judgeDay) { judgeDay = day; judgeCount = 0 }
    if (judgeCount >= JUDGE_DAILY_MAX || Date.now() - judgeAt < JUDGE_MIN_GAP_MS) return
    const llm = ctx.get('llm')
    const defaultModel = ctx.get('agentDefaultModel')
    if (!llm || !defaultModel || typeof llm.stream !== 'function') return
    const selection = defaultModel.currentSelection()
    if (!selection || !selection.provider || !selection.model) return
    judgeBusy = true
    judgeAt = Date.now()
    judgeCount += 1
    try {
      const prompt = await loadJudgePrompt()
      const userText = `对方这轮说：\n${cacheItem.user}\n\n你这轮回复：\n${cacheItem.reply}\n\n请判断你这一轮的心情变化，只输出 JSON。`
      const raw = await streamText({
        provider: selection.provider,
        model: selection.model,
        system: String(prompt.system || ''),
        messages: [{ role: 'user', content: [{ type: 'text', text: userText }] }],
        maxTokens: JUDGE_MODEL_MAX_TOKENS,
        temperature: 0,
      })
      const parsed = parseJudge(raw)
      if (!parsed) return
      const dims = {}
      for (const key of ('happy', 'calm', 'sweet', 'heartache', 'worry', 'sad', 'jealous')) {
        const value = Number(parsed.dims && parsed.dims[key])
        dims[key] = Number.isFinite(value) ? Math.max(-10, Math.min(10, value)) : 0
      }
      const moodDelta = Number(parsed.moodDelta)
      const confidence = Number(parsed.confidence)
      if (!Number.isFinite(moodDelta) || !Number.isFinite(confidence)) return
      await api.dshMoodEvent({
        moodDelta: Math.max(-10, Math.min(10, moodDelta)),
        dims,
        confidence: Math.max(0, Math.min(100, Math.round(confidence))),
        reason: typeof parsed.reason === 'string' ? parsed.reason.slice(0, 60) : '',
        session: { id, title: cacheItem.title || '' },
      })
    } catch (error) {
      ctx.logger.warn('deepfeiyu: 融合心情判断失败 %s', error?.message || error)
    } finally {
      judgeBusy = false
    }
  }

  async function refreshMood() {
    if (disposed || (moodAt && Date.now() - moodAt < MOOD_REFRESH_MS)) return
    try {
      const state = await api.dshState()
      moodAt = Date.now()
      const mood = state && state.mood ? state.mood : null
      if (!mood || typeof mood.mood !== 'number') { moodText = ''; return }
      const last = state.timeline && state.timeline[0] ? state.timeline[0] : null
      const reason = last && last.source === 'dsh' && last.reason ? `；刚才：${String(last.reason).slice(0, 60)}` : ''
      moodText = `（你此刻的心情：${Math.round(mood.mood)}/100${reason}）`
    } catch {
      moodText = ''
    }
  }

  const systemPrompt = ctx.get('systemPrompt')
  if (systemPrompt && typeof systemPrompt.context === 'function') {
    disposers.push(systemPrompt.context({
      name: 'deepfeiyu.mood',
      order: 50,
      text: () => moodText,
    }))
  }

  if (timer && typeof timer.interval === 'function') {
    disposers.push(timer.interval(() => { void refreshMood() }, MOOD_REFRESH_MS))
  }
  // 没有 timer 服务时不做定时刷新：turn/end 与 user/message 分支会主动调 refreshMood()。
```

再在 `session/event` 的 `turn/end` 分支末尾加 `void judgeSession(session)` 与 `void refreshMood()`；在 `user/message` 分支末尾加 `void refreshMood()`。

- [ ] **步骤 4：`dispose()` 清理**

```js
  function dispose() {
    disposed = true
    for (const disposeFn of disposers.splice(0)) {
      try { if (typeof disposeFn === 'function') disposeFn() } catch {}
    }
  }
```

- [ ] **步骤 5：运行验证**

运行：
```powershell
node --check dsh-plugin/lib/fusion.js
.venv\Scripts\python.exe -m pytest tests/test_dsh_plugin.py -q
```
预期：node 0；pytest PASS

- [ ] **步骤 6：用假 ctx 脚本冒烟（不入库）**

在 `%TEMP%` 写一个 `fusion-smoke.mjs`：用假 `ctx.on`、假 `ctx.get('llm')`（返回 `text-delta` 流：`{"moodDelta":4,...}`）、假 `api`，触发一次 `session/event` `turn/end`，断言 `api.dshMoodEvent` 被调用且参数在范围内；再断言 `dispose()` 后不再调用。运行 `node fusion-smoke.mjs`，输出 ALL PASS。

- [ ] **步骤 7：Commit**

```bash
git add dsh-plugin/lib/fusion.js tests/test_dsh_plugin.py
git commit -m "feat(dsh-plugin): Host 判断共享心情并注入 Harness 提示词"
```

---

## 任务 7：DSH 设置页「心情与桌宠（融合）」

**文件：**
- 修改：`dsh-plugin/client.js`
- 测试：`tests/test_dsh_plugin.py`

- [ ] **步骤 1：编写失败的静态测试（追加）**

```python
def test_settings_page_has_fusion_section():
    source = (PKG / "client.js").read_text(encoding="utf-8")
    for token in ("心情与桌宠（融合）", "fusion.state", "fusion.config",
                  "fusion.timeline", "绑定账号", "允许 Harness 来历进微信"):
        assert token in source, f"client.js 缺少 {token}"
```

- [ ] **步骤 2：运行测试验证失败**

运行：`.venv\Scripts\python.exe -m pytest tests/test_dsh_plugin.py -q`
预期：FAIL

- [ ] **步骤 3：实现设置页区块**

在 `DeepFeiyuSettingsPage` 内加状态与加载函数（放在 `dshPlugins` 状态之后）：

```js
          const [fusion, setFusion] = React.useState(null);
          const [fusionError, setFusionError] = React.useState('');
          const [fusionPending, setFusionPending] = React.useState(false);

          async function loadFusion() {
            try {
              const value = await call('fusion.state', {}, 15000);
              if (!mounted.current) return;
              setFusion(value && typeof value === 'object' ? value : null);
              setFusionError('');
            } catch (error0) {
              if (!mounted.current) return;
              setFusion(null);
              setFusionError(errorMessage(error0));
            }
          }

          async function saveFusion(patch) {
            if (fusionPending) return;
            setFusionPending(true);
            setFusionError('');
            try {
              const value = await call('fusion.config', patch, 15000);
              if (!mounted.current) return;
              setFusion(value && typeof value === 'object' ? value : null);
              await loadFusion();
            } catch (error0) {
              if (mounted.current) setFusionError(errorMessage(error0));
            } finally {
              if (mounted.current) setFusionPending(false);
            }
          }
```

在 `tick` 里加 `void loadFusion();`。

渲染函数加在 `renderDshPluginsSection` 之后：

```js
          function renderFusionSection() {
            const config = fusion && fusion.config ? fusion.config : {};
            const accounts = fusion && Array.isArray(fusion.accounts) ? fusion.accounts : [];
            const mood = fusion && fusion.mood ? fusion.mood : null;
            const timeline = fusion && Array.isArray(fusion.timeline) ? fusion.timeline.slice(0, 8) : [];
            const selected = fusion && fusion.accountId ? String(fusion.accountId) : '';
            const toggles = [
              ['judge_enabled', 'DSH 判断心情', 'Harness 回合结束后用默认模型判断，写回共享库'],
              ['pet_events_enabled', '宠物反映 Harness', '工具调用/回合结束时驱动同一只桌宠'],
              ['cross_reason_enabled', '允许 Harness 来历进微信', '只带 ≤60 字理由；群聊永远只说心情'],
            ];
            return h('section', { className: 'dsh-deepfeiyu-dep-terminal-card' },
              h('div', { className: 'dsh-deepfeiyu-dep-terminal-head' },
                h('span', { className: 'dsh-deepfeiyu-dep-terminal-title' }, '心情与桌宠（融合）'),
                settingsButton('刷新', () => { void loadFusion(); }, { title: '刷新融合状态' })),
              h('p', { className: 'dsh-deepfeiyu-dep-note' },
                '绑定账号后，Harness 与微信共享同一份心情；桌宠由控制台进程渲染，这里只控制与查看。'),
              fusionError ? h('p', { className: 'dsh-deepfeiyu-dep-failure', role: 'alert' }, fusionError) : null,
              accounts.length
                ? h('div', { className: 'dsh-deepfeiyu-dep-row' },
                  h('div', { className: 'dsh-deepfeiyu-dep-main' },
                    h('div', { className: 'dsh-deepfeiyu-dep-head' },
                      h('span', { className: 'dsh-deepfeiyu-dep-name' }, '绑定账号'),
                      fusion && fusion.boundFallback
                        ? h('span', { className: 'dsh-deepfeiyu-badge' }, '已回落到当前账号')
                        : null),
                    h('p', { className: 'dsh-deepfeiyu-dep-desc' }, 'DSH 侧心情读写的微信账号')),
                  h('div', { className: 'dsh-deepfeiyu-dep-actions' },
                    h('select', {
                      className: 'dsh-deepfeiyu-input',
                      value: selected,
                      disabled: fusionPending,
                      onChange: (event) => { void saveFusion({ account_id: event.target.value }); },
                    }, accounts.map((account) => h('option', {
                      key: String(account.id), value: String(account.id),
                    }, String(account.name) + (account.active ? '（当前）' : ''))))))
                : null,
              h('div', { className: 'dsh-deepfeiyu-dep-list' }, toggles.map(([key, label, desc]) =>
                h('div', { key, className: 'dsh-deepfeiyu-dep-row' },
                  h('div', { className: 'dsh-deepfeiyu-dep-main' },
                    h('div', { className: 'dsh-deepfeiyu-dep-head' },
                      h('span', { className: 'dsh-deepfeiyu-dep-name' }, label)),
                    h('p', { className: 'dsh-deepfeiyu-dep-desc' }, desc)),
                  h('div', { className: 'dsh-deepfeiyu-dep-actions' },
                    settingsButton(config[key] ? '开' : '关', () => {
                      void saveFusion({ [key]: !config[key] });
                    }, { disabled: fusionPending, title: '切换' + label }))))),
              mood ? h('p', { className: 'dsh-deepfeiyu-dep-desc' },
                '当前心情：' + Math.round(Number(mood.mood) || 0) + '/100') : null,
              timeline.length
                ? h('div', { className: 'dsh-deepfeiyu-dep-list' }, timeline.map((row, index) =>
                  h('p', { key: String(row.id || index), className: 'dsh-deepfeiyu-dep-note' },
                    (row.origin === 'harness' ? 'Harness' : '微信') + ' · '
                    + (row.reason ? String(row.reason).slice(0, 60) : '自然平复'))))
                : null);
          }
```

在设置页 `return` 的末尾（`renderDshPluginsSection()` 之后）加 `renderFusionSection()`。

- [ ] **步骤 4：运行测试验证通过**

运行：
```powershell
node --check dsh-plugin/client.js
.venv\Scripts\python.exe -m pytest tests/test_dsh_plugin.py -q
```
预期：node 0；pytest PASS

- [ ] **步骤 5：Commit**

```bash
git add dsh-plugin/client.js tests/test_dsh_plugin.py
git commit -m "feat(dsh-plugin): 设置页融合区块（绑定/开关/时间线）"
```

---

## 任务 8：DSH 右下角心情挂饰

**文件：**
- 修改：`dsh-plugin/client.js`
- 测试：`tests/test_dsh_plugin.py`

- [ ] **步骤 1：编写失败的静态测试（追加）**

```python
def test_client_registers_mood_overlay():
    source = (PKG / "client.js").read_text(encoding="utf-8")
    for token in ("DeepFeiyuMoodWidget", "shell.overlay", "deepfeiyu-mood",
                  "fusion.state", "20_000"):
        assert token in source, f"client.js 缺少 {token}"
```

- [ ] **步骤 2：运行测试验证失败**

运行：`.venv\Scripts\python.exe -m pytest tests/test_dsh_plugin.py -q`
预期：FAIL

- [ ] **步骤 3：实现挂饰组件**

在 `DeepFeiyuSettingsPage` 之后加：

```js
        // 右下角心情挂饰：只读共享状态，20s 轮询；控制台不可达时显示“—”。
        function DeepFeiyuMoodWidget() {
          const mounted = React.useRef(true);
          const [state, setState] = React.useState(null);
          const [open, setOpen] = React.useState(false);

          async function load() {
            try {
              const value = await call('fusion.state', {}, 15000);
              if (!mounted.current) return;
              setState(value && typeof value === 'object' ? value : null);
            } catch {
              if (mounted.current) setState(null);
            }
          }

          React.useEffect(() => {
            mounted.current = true;
            void load();
            const timer = window.setInterval(() => {
              if (typeof document !== 'undefined' && document.visibilityState === 'hidden') return;
              void load();
            }, 20_000);
            return () => {
              mounted.current = false;
              window.clearInterval(timer);
            };
          }, []);

          const mood = state && state.mood ? state.mood : null;
          const value = mood ? Math.round(Number(mood.mood) || 0) : null;
          const face = value === null ? '·' : value >= 75 ? '☺' : value >= 50 ? '◡' : '·';
          const timeline = state && Array.isArray(state.timeline) ? state.timeline.slice(0, 5) : [];
          return h('div', { className: 'dsh-deepfeiyu-mood' },
            open ? h('div', { className: 'dsh-deepfeiyu-mood-panel' },
              h('p', { className: 'dsh-deepfeiyu-dep-name' }, '心情 ' + (value === null ? '—' : value + '/100')),
              timeline.map((row, index) => h('p', {
                key: String(row.id || index), className: 'dsh-deepfeiyu-dep-note',
              }, (row.origin === 'harness' ? 'Harness' : '微信') + ' · '
                + (row.reason ? String(row.reason).slice(0, 40) : '自然平复')))) : null,
            h('button', {
              type: 'button',
              className: 'dsh-deepfeiyu-mood-button',
              'aria-label': '心情挂饰',
              onClick: () => setOpen((previous) => !previous),
            },
            h('span', { className: 'dsh-deepfeiyu-mood-face' }, face),
            h('span', { className: 'dsh-deepfeiyu-mood-value' }, value === null ? '—' : String(value))));
        }
```

样式加进 `STYLES`：

```css
.dsh-deepfeiyu-mood { position: fixed; right: 20px; bottom: 72px; z-index: 40; display: flex; flex-direction: column; align-items: flex-end; gap: 8px; }
.dsh-deepfeiyu-mood-button { display: inline-flex; align-items: center; gap: 6px; padding: 6px 10px; border: 1px solid var(--dsw-alias-border-l2, #0000001a); border-radius: 999px; background: var(--dsw-alias-bg-layer-1, #fff); color: var(--dsw-alias-label-primary, #0f1115); font: inherit; cursor: pointer; }
.dsh-deepfeiyu-mood-face { font-size: 16px; }
.dsh-deepfeiyu-mood-value { font-size: 12px; font-variant-numeric: tabular-nums; }
.dsh-deepfeiyu-mood-panel { width: 220px; padding: 10px 12px; border: 1px solid var(--dsw-alias-border-l2, #0000001a); border-radius: 12px; background: var(--dsw-alias-bg-layer-1, #fff); }
```

注册（放在其他 `ctx.effect` 之后）：

```js
        ctx.effect(() => ctx.slots.inject('shell.overlay', () => ctx.slots.register({
          name: 'shell.overlay',
          id: 'deepfeiyu-mood',
          order: 60,
          label: () => '心情',
        }, DeepFeiyuMoodWidget)), 'deepfeiyu.mood-widget');
```

- [ ] **步骤 4：运行验证**

运行：
```powershell
node --check dsh-plugin/client.js
.venv\Scripts\python.exe -m pytest tests/test_dsh_plugin.py -q
```
预期：node 0；pytest PASS

- [ ] **步骤 5：Commit**

```bash
git add dsh-plugin/client.js tests/test_dsh_plugin.py
git commit -m "feat(dsh-plugin): 右下角共享心情挂饰"
```

---

## 任务 9：文档、全量测试与手工验收

**文件：**
- 修改：`README.md`、`docs/使用说明.md`、`CHANGELOG.md`、`dsh-plugin/README.md`
- 测试：全量

- [ ] **步骤 1：更新文档**

`README.md` 7.5 节「设置页」段落补：融合区块（绑定账号、三个开关、共享时间线）与右下角挂饰；安全说明补「Harness 原文不出 Host、只传判断结果与 ≤60 字理由、群聊降级」。

`docs/使用说明.md` 16.1 补融合能力；16.4 补故障排查：

```markdown
- **融合区块显示控制台未运行**：先点「启动控制台」；判断与宠物事件在控制台停机期间直接丢弃，不会阻塞 Harness。
- **挂饰显示“—”**：控制台不可达或心情未开启；检查「设置 → 心情」是否启用。
- **Harness 来历出现在微信里**：这是「允许 Harness 来历进微信」开关控制的；关掉后只保留心情数值，群聊永远不带来历。
```

`CHANGELOG.md` 新增 2026-11-02 条目（控制台桥接、心情重构、宠物事件、DSH 设置页/挂饰、隐私规则），验证状态写真实结果。

`dsh-plugin/README.md` 新增「融合：共享心情与桌宠」小节：宿主事件来源、判断模型与限流、三个开关、隐私边界、上游 bundle 标注独立版。

- [ ] **步骤 2：跑全量测试**

运行：`.venv\Scripts\python.exe -m pytest -q`
预期：通过数 ≥ 基线 + 新增；失败仅为本机环境性（PySide6 / wechatauto / `vendor/dsh-whale-widget` 缺失），逐条如实记录。

- [ ] **步骤 3：手工验收（按规格 §10）**

1. 启动控制台与 DSH；DSH 里完成一轮对话 → 控制台时间线出现 `source=dsh`。
2. 挂饰 20s 内更新数值；点开显示双源时间线。
3. Harness 工具调用 → 桌宠 WORKING；气泡带「Harness · 标题」。
4. 微信私聊回复：开关开 → 注入一句 Harness 来历；关 → 只剩数值；群聊永远只有数值。
5. 停掉控制台 → DSH 设置页显示启动按钮，Harness 对话正常。
6. 卸载/热重载插件后检查没有重复心情注入（systemPrompt.context 已清理）。

- [ ] **步骤 4：Commit**

```bash
git add README.md docs/使用说明.md CHANGELOG.md dsh-plugin/README.md
git commit -m "docs: 记录心情/桌宠双端融合的使用与隐私边界"
```

---

## 自检记录

- 规格覆盖：§2 决策 → 任务 1/3/4/6/7；§3 技术事实 → 任务 6/7；§6 控制台 → 任务 1–4；§7 DSH → 任务 5–8；§8 隐私 → 任务 3/8/9；§9 失败 → 任务 4/6；§10 测试 → 任务 2/4/7/9；§11 非目标 → 未实现上游改造、未做多账号；§12 分期 → P1=任务 1–7，P2=任务 8；§13 回滚 → 新模块可删。
- 无占位符：所有步骤都给了可执行代码或具体命令。
- 类型一致：`apply_judged_delta` / `last_event` / `publish_pet_event` / `fusion.state|config|timeline` / `dshMoodEvent` 等命名在任务间一致。
- 已知风险：DSH `session/event` 的 `session.header.title` 可能没有标题字段（取不到时用空串）；`ctx.get('timer')` 不可用时挂饰/提示词注入仍依赖 20s 客户端轮询与事件刷新，不阻塞主流程。

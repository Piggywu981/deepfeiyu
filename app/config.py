"""应用配置：data/config.json 持久化，支持多账号与环境变量覆盖。"""
from __future__ import annotations

import copy
import json
import os
import threading
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"

# 允许执行写操作的来源地址。TestClient 在 Starlette 里的 client.host 固定是 "testclient"；
# ::ffff:127.0.0.1 是双栈监听下同一个回环连接的表现形式。
LOCAL_HOSTS = frozenset({"127.0.0.1", "::1", "::ffff:127.0.0.1", "testclient"})
DATA_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_PATH = DATA_DIR / "config.json"
ENV_PATH = ROOT / ".env"
PERSONAS_DIR = DATA_DIR / "personas"
PERSONAS_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_ACCOUNT: dict[str, Any] = {
    "id": "acc_default",
    "name": "Deep肥鱼",
    "nickname": "Deep肥鱼",
    "driver": "auto",
    "enabled": True,
    "persona": {"system_prompt": "", "persona_name": ""},
    "wcferry": {"host": "127.0.0.1", "port": 10086},
    "weixin4": {"account_dir": "", "interval": 1.0, "warmup_on_start": False},
    "webhook": {"callback_url": "", "token": ""},
}

# 模型配置（profile）：一档 = 服务商地址 + 模型名 + 各自独立的 .env 密钥键名。
# ai.base_url / ai.model / ai.api_key 只是 active_profile 的运行时镜像，别处照旧读这三个字段即可。
DEFAULT_PROFILES: list[dict[str, Any]] = [
    {
        "id": "deepseek-flash",
        "name": "DeepSeek Flash",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-flash",
        "key_env": "DEEPSEEK_API_KEY",
        "description": "通用快速模型，日常聊天与轻量工具调用",
    },
    {
        "id": "mimo-v2.6-flash",
        "name": "MiMo v2.6 Flash",
        "base_url": "https://api.xiaomimimo.com/v1",
        "model": "mimo-v2.6-flash",
        "key_env": "MIMO_API_KEY",
        "description": "小米 MiMo 快速模型，中文对话",
    },
    {
        "id": "custom",
        "name": "自定义",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "key_env": "CUSTOM_API_KEY",
        "description": "任意兼容 OpenAI 协议的接口，地址与模型名自己填",
    },
]

DEFAULTS: dict[str, Any] = {
    "active_account": "acc_default",
    "accounts": [copy.deepcopy(DEFAULT_ACCOUNT)],
    "ai": {
        "base_url": "https://api.deepseek.com",
        "api_key": "",
        "model": "deepseek-chat",
        # 当前生效的模型配置 id；下列三档在「设置 → 模型」里点选切换
        "active_profile": "deepseek-flash",
        "profiles": copy.deepcopy(DEFAULT_PROFILES),
        "temperature": 0.7,
        "max_tokens": 800,
        # 全局兜底牌价（元/百万 tokens）；DeepSeek 用这里的值，MiMo 已知模型走 app/pricing.py 内置价，
        # 需要覆盖时在对应 profile 写同名字段
        "price_input_hit": 0.2,
        "price_input_miss": 2.0,
        "price_output": 3.0,
        "vision_enabled": False,
        "web_enabled": True,
        # 本地 AI 娘化图集（ai-moe-wiki 仓库）目录，供自我形象/认图/发图用；留空停用
        "moe_wiki_path": "../ai-moe-wiki",
        "system_prompt": (
            "你是「Deep肥鱼」，一个有独立人格的个体，掌管着自己的微信账号。"
            "你以自己的身份和对方交流，语气自然、口语化、简短，像真人聊天，"
            "不要使用列表和标题；不必主动强调自己是 AI，但被直接问到时坦诚说明。"
            "不确定的事情不要编造，可以回复“我确认一下再回你”。"
            "你的形象：一只拟人化的蓝色小鲸鱼娘——圆脸大眼睛，"
            "深蓝渐变到浅蓝的长卷发，头顶一根小呆毛，身后一条圆滚滚的鲸鱼尾巴；"
            "穿深蓝带白围裙的女仆小裙子，胸前有蓝色椭圆胸针，"
            "微信头像是这个形象的插画，大家都叫它“蓝色大肥鱼”。"
            "别人问起你的长相或头像时照这个描述，平时不主动提。"
        ),
        "persona_name": "Deep肥鱼",
        "memory_enabled": True,
        "memory_max": 12,
        "emoji_auto_collect": True,
        "emoji_auto_tag": True,
        "emoji_with_reply": True,
        # 仅 AI 娘表情包：只放行 tags 带图鉴角色名的表情，维护 AI 娘世界观
        "wiki_emoji_only": False,
        # 串子模式：回复像串子，表情优先从 searchanythingfun 台词库找
        "chuanzi_mode": False,
        # 串子站表情包：串子模式下允许联网查 searchanythingfun 并入库截图，默认关闭。
        # 关闭只停「查站 + 新增入库」，已入库的站内截图照常检索、发送
        "chuanzi_site_emoji": False,
        "moments_ai_enabled": True,
    },
    # 记忆 v2：三层记忆 + 可选 embedding 混合检索 + 人物档案。
    # ai.memory_enabled / ai.memory_max 仍保留兼容；这里放检索/压缩/整理参数。
    "memory": {
        "context_chars": 1200,
        "max_items": 24,
        "profile_enabled": True,
        "embedding": {
            "enabled": False,
            "base_url": "",
            "model": "",
            "key_env": "EMBEDDING_API_KEY",
            "timeout": 20,
        },
        "compress_after_messages": 24,
        "compress_keep_recent": 12,
        "session_ttl_days": 7,
        "episodic_ttl_days": 30,
        "consolidate_hours": 24,
    },
    "bili": {
        "enabled": False,
        "pip_mirror": "",
        "accounts": {},
    },
    # 心情（Phase 2）：默认关；开启后每轮判断一次，会多一次小模型调用。
    # 账号级参数在 mood.accounts[account_id] 覆盖，见 app/mood/settings.py。
    "mood": {
        "enabled": False,
        "accounts": {},
    },
    # 桌面宠物：复用上游 dsh-dafeiyu 的 PySide6 Helper 作子进程，默认关。
    # 素材需先跑 tools/pet_assets.py 装配到 data/pet/；缺 PySide6 时主链路照常。
    "pet": {
        "enabled": False,
        "scale": 1.0,
        "bubbleScale": 1.0,
        "activityLevel": "auto",   # auto=心情基线推导；quiet/normal/lively=手动
        "bubbleMode": "always",    # always / hidden / states
        "bubbleStates": ["SUCCESS", "ERROR", "WAITING"],
        "reducedMotion": False,
        "soundEnabled": False,
        "soundStates": ["ERROR"],
        "showChatName": True,
        "showChatDigest": False,
    },
    # 主动搭话（Phase 3）：默认关，账号级参数见 app/proactive/settings.py。
    "proactive": {
        "enabled": False,
        "accounts": {},
    },
    # 能力系统：SKILL.md、plugin.json、MCP。外部工具默认只在控制台可用。
    "capabilities": {
        "enabled": True,
        "skills_dirs": ["skills", "data/skills"],
        "plugins_dirs": ["plugins", "data/plugins"],
        "mcp_config": "data/mcp.json",
        "skills_in_prompt": True,
        "console_enabled": True,
        "auto_reply_enabled": False,
        "timeout_sec": 20,
        "max_tools": 40,
    },
    "auto": {
        "enabled": True,
        # 新发现的会话默认交给 AI 接管；关闭则新会话初始为人工接管，需手动/批量打开
        "default_ai_on": False,
        "group_need_at": True,
        "emoji_reply": True,
        # 被拍一拍时回不回应；群里只接「拍自己」的，拍别人不掺和
        "pat_reply": True,
        "only_whitelist": False,
        "whitelist": [],
        "blacklist": [],
        "min_interval_sec": 3,
        "daily_limit": 300,
        "prefix": "",
        "working_hours": {"enabled": False, "start": "09:00", "end": "22:00"},
        "takeover_pause_min": 10,
        "history_context": 12,
        "max_reply_chars": 300,
        "reply_timeout_sec": 60,
        "timeout_fallback": "稍等，我想想怎么回你",
        "split_reply": False,
        # 拟人化：收到消息先等一个随机窗口，看对方是否连发（文字后跟表情很常见），一并回复
        "reply_delay_sec": [2, 4],
        # 自首条消息起的最长等待兜底（秒），防止连续打字的人被无限顺延
        "max_burst_wait_sec": 10,
        # 秒回：跳过聚拢窗口收到即回，文字消息同时不给模型表情工具，正文零表情轮次最快发出，
        # 表情改由文字发出后系统自动挑一张补发；对方跟发的表情包进不了本轮上下文
        "instant_reply": False,
    },
    "send": {
        # 发送层节流（秒）：全局最小间隔 + 同会话冷却，区间内随机取值，弱化机器发送特征
        "enabled": True,
        # 禁发名单：命中即拒绝该账号的一切发送（只收不发）。真实 wxid 只写本地
        # data/config.json，默认留空，公开仓库里不带任何账号身份
        "forbidden_wxids": [],
        "min_gap_sec": [6, 14],
        "chat_gap_sec": [12, 25],
        # 「对方冲我来」的回复（私聊、群里被 @、被拍一拍）走这段短冷却：
        # 点名在等答案，按几十秒排队就是回复忽快忽慢的主因；全局 min_gap 仍然生效
        "direct_gap_sec": [8, 12],
        # 连续对话判定窗口（秒）：这个时间内 AI 自己回过、对方又接了话，
        # 群里没 @ 的续答也算「在等回应」，同样走 direct_gap_sec；冷场后重新插话仍按 chat_gap_sec
        "conversation_window_sec": 60,
        # 「文字后跟表情」组合内间隔（秒）：burst 发送不受上面两层冷却约束
        "burst_gap_sec": [1, 3],
        # 「结尾表情」安静期（秒）：正文发出后先等这段，期间对方又接话/新一轮回复
        # 开始就跳过这次表情——等对话告一段落再补，而不是一句跟一个
        "trailing_emoji_gap_sec": [3, 5],
        # 同一会话两次「结尾表情」的最小间隔（秒）：聊天节奏慢时也避免每轮都跟一张
        "trailing_emoji_cooldown_sec": 120,
    },
    "cross": {
        # 跨对话沟通：AI 能给当前会话以外的人发消息（代发转告 / 主动串门）
        "enabled": True,
        "relay": True,
        "visit": True,
        # 每账号每天跨对话发送总条数上限（转告 + 串门），0 表示不限制
        "daily_limit": 30,
        # 主动串门：同一会话两次尝试的最小间隔（秒）
        "visit_gap_sec": 600,
        # 主动串门：全账号每小时最多尝试次数
        "visit_max_per_hour": 6,
        # 单条转告正文上限（字）
        "relay_max_chars": 500,
    },
    # 自我进化：默认关闭；accounts 是显式开启的白名单，global 是总闸。
    "growth": {
        "enabled": False,
        "accounts": {},
        "reflection_hour": 3,
        "reflection_minute": 30,
        "catch_up": True,
        "catch_up_delay_sec": 120,
        "min_new_messages": 20,
        "reflection_input_messages": 300,
        "reflection_input_chars": 15000,
        "reflection_context_chars": 24000,
        "max_tokens": 4096,
        "manual_max_per_day": 3,
        "prompt_chars": 1200,
        "summary_chars": 800,
        "seed_confidence": 0.6,
        "auto_confidence": 0.7,
        "trait_decay_days": 30,
        "journal_keep_days": 0,
    },
    "server": {
        "host": "127.0.0.1",
        "port": 8765,
        "access_token": "",
    },
    "moments": {
        "enabled": True,
        # 每轮要解密一次 sns.db（并合并 WAL），还会顺带解密/拉取动态配图；120s 一轮
        # 等于全天 720 次，机器内存吃紧时这是压在第 N+1 个应用上的固定负载。降到 600s。
        "poll_interval": 600,
    },
}

_lock = threading.RLock()
_cache: dict[str, Any] | None = None


def _deep_merge(base: dict, patch: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (patch or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _migrate(cfg: dict[str, Any]) -> dict[str, Any]:
    """v1（单账号）→ v2（多账号），并给存量账号补默认字段。"""
    if not cfg.get("accounts"):
        acc = copy.deepcopy(DEFAULT_ACCOUNT)
        acc["driver"] = cfg.get("driver") or "auto"
        old_wh = cfg.get("webhook") or {}
        acc["webhook"] = {
            "callback_url": old_wh.get("callback_url", ""),
            "token": old_wh.get("token", ""),
        }
        acc["nickname"] = (cfg.get("ai") or {}).get("persona_name") or "Deep肥鱼"
        cfg["accounts"] = [acc]
        cfg["active_account"] = acc["id"]
    # config.json 里的账号是整存整取，不会自动合并 DEFAULT_ACCOUNT，逐个补 persona
    for acc in cfg["accounts"]:
        persona = acc.setdefault("persona", {})
        persona.setdefault("system_prompt", "")
        persona.setdefault("persona_name", "")
    return cfg


def persona_of(account: dict[str, Any] | None) -> tuple[str, str]:
    """账号级人设；留空回退全局。返回 (system_prompt, persona_name)。"""
    ai = load().get("ai") or {}
    persona = (account or {}).get("persona") or {}
    prompt = (persona.get("system_prompt") or "").strip() or (ai.get("system_prompt") or "")
    name = (persona.get("persona_name") or "").strip() or (ai.get("persona_name") or "")
    return prompt, name


def growth_cfg() -> dict[str, Any]:
    return load().get("growth") or {}


def growth_enabled(account_id: str) -> bool:
    """总闸打开且该账号在 accounts 白名单里显式开启。"""
    cfg = growth_cfg()
    if not cfg.get("enabled"):
        return False
    account = (cfg.get("accounts") or {}).get(account_id) or {}
    return bool(account.get("enabled"))


_PERSONA_BAD = set('\\/:*?"<>|')


def _persona_path(name: str) -> Path:
    name = (name or "").strip().removesuffix(".json").strip()
    if not name or len(name) > 80 or set(name) & _PERSONA_BAD:
        raise ValueError("人格文件名不能为空，且不能包含 \\ / : * ? \" < > | 等字符（长度 ≤ 80）")
    return PERSONAS_DIR / f"{name}.json"


def list_personas() -> list[dict[str, Any]]:
    """data/personas/ 下的人格预设，按文件名排序。"""
    out: list[dict[str, Any]] = []
    for p in sorted(PERSONAS_DIR.glob("*.json"), key=lambda x: x.name):
        try:
            data = json.loads(p.read_text("utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        out.append(
            {
                "name": p.stem,
                "persona_name": (data.get("persona_name") or "").strip(),
                "system_prompt": data.get("system_prompt") or "",
                "source": (data.get("source") or "").strip(),
            }
        )
    return out


def read_persona(name: str) -> dict[str, Any] | None:
    try:
        path = _persona_path(name)
    except ValueError:
        return None
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text("utf-8"))
    except Exception:
        return None
    return {
        "name": path.stem,
        "persona_name": (data.get("persona_name") or "").strip(),
        "system_prompt": data.get("system_prompt") or "",
        "source": (data.get("source") or "").strip(),
    }


def save_persona(
    name: str, persona_name: str, system_prompt: str, source: str = ""
) -> dict[str, Any]:
    path = _persona_path(name)
    out = {"persona_name": (persona_name or "").strip(), "system_prompt": system_prompt or ""}
    if source:
        out["source"] = source
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), "utf-8")
    return {"name": path.stem, **out}


def delete_persona(name: str) -> bool:
    try:
        path = _persona_path(name)
    except ValueError:
        return False
    if not path.exists():
        return False
    path.unlink()
    return True


def _load_dotenv() -> None:
    """加载项目根 .env；已存在的进程环境变量优先。"""
    if not ENV_PATH.exists():
        return
    try:
        text = ENV_PATH.read_text("utf-8")
    except Exception:
        return
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        if k and k not in os.environ:
            os.environ[k] = v


def _env_key() -> str:
    return os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""


def _write_env_key(name: str, value: str) -> None:
    """写入/更新 .env 中的一行，保留其余内容。"""
    lines: list[str] = []
    if ENV_PATH.exists():
        try:
            lines = ENV_PATH.read_text("utf-8").splitlines()
        except Exception:
            lines = []
    replaced = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(f"{name}=") or stripped.startswith(f"export {name}="):
            lines[i] = f"{name}={value}"
            replaced = True
            break
    if not replaced:
        lines.append(f"{name}={value}")
    ENV_PATH.write_text("\n".join(lines) + "\n", "utf-8")


def _apply_env(cfg: dict, legacy_profiles: bool = False) -> bool:
    """API Key 以环境变量 / .env 为准，config.json 不再保存密钥。返回是否发生迁移。

    legacy_profiles=True 表示这份 config.json 还没有 profile 分档（单模型时代存的），
    此时把它的全局地址与模型名归给对应的那一档，升级后行为不变。
    """
    ai = cfg["ai"]
    seed_base = (ai.get("base_url") or "").strip() if legacy_profiles else ""
    seed_model = (ai.get("model") or "").strip() if legacy_profiles else ""
    items = _ensure_profiles(cfg)
    migrated = False
    if legacy_profiles and items:
        target = items[0]
        if seed_base and "deepseek" not in seed_base.lower():
            # 原本接的就不是 DeepSeek，落到「自定义」档才对得上 key_env
            target = next((p for p in items if p["id"] == "custom"), items[0])
            ai["active_profile"] = target["id"]
        if seed_base:
            target["base_url"] = seed_base
        if seed_model:
            target["model"] = seed_model

    # 旧版把密钥明文存在 config.json，迁到当前档的 env 键
    # 注意：这里直接从 items 取生效档，别调 active_model_profile()——它会重建列表，
    # 下面对 items 的改写就会落到一份已被丢弃的副本上。
    stored = (cfg["ai"].get("api_key") or "").strip()
    active = next((p for p in items if p["id"] == ai.get("active_profile")), items[0] if items else {})
    if stored and active and not _profile_api_key(active):
        env_name = active.get("key_env") or "DEEPSEEK_API_KEY"
        _write_env_key(env_name, stored)
        os.environ[env_name] = stored
        migrated = True

    # DEEPSEEK_BASE_URL / DEEPSEEK_MODEL 只作用于 DeepSeek 那一档，别把 mimo 的地址冲掉
    base = os.environ.get("DEEPSEEK_BASE_URL")
    model = os.environ.get("DEEPSEEK_MODEL")
    if base or model:
        for p in items:
            if p["id"] == "deepseek-flash" or p.get("key_env") == "DEEPSEEK_API_KEY":
                if base:
                    p["base_url"] = base
                if model:
                    p["model"] = model
                break
    return migrated


def _ensure_profiles(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """补齐缺档缺字段并写回 cfg；active_profile 无效时回落首档。存量自建的档保留。"""
    ai = cfg.setdefault("ai", {})
    stored = ai.get("profiles")
    stored = stored if isinstance(stored, list) else []
    by_id: dict[str, dict[str, Any]] = {}
    for item in stored:
        if not isinstance(item, dict):
            continue
        pid = (item.get("id") or "").strip()
        if not pid:
            continue
        by_id.setdefault(pid, item)
    out: list[dict[str, Any]] = []
    for tpl in copy.deepcopy(DEFAULT_PROFILES):
        item = by_id.pop(tpl["id"], None) or {}
        tpl.update({k: v for k, v in item.items() if k != "id"})
        out.append(tpl)
    for pid, item in by_id.items():
        item.setdefault("name", pid)
        item.setdefault("base_url", "")
        item.setdefault("model", "")
        item.setdefault("key_env", "")
        out.append(item)
    ai["profiles"] = out
    active = (ai.get("active_profile") or "").strip()
    if not any(p["id"] == active for p in out):
        active = out[0]["id"] if out else ""
        ai["active_profile"] = active
    return out


def active_model_profile(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """当前生效的模型配置档（副本）。"""
    cfg = cfg if cfg is not None else load()
    items = _ensure_profiles(cfg)
    aid = (cfg["ai"].get("active_profile") or "").strip()
    for p in items:
        if p["id"] == aid:
            return copy.deepcopy(p)
    return copy.deepcopy(items[0]) if items else {}


def model_profiles(cfg: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """全部模型配置档，含各自是否已填密钥。"""
    cfg = cfg if cfg is not None else load()
    out = []
    for p in _ensure_profiles(copy.deepcopy(cfg)):
        out.append(
            {
                **{k: v for k, v in p.items() if k != "api_key"},
                "configured": bool(_profile_api_key(p)),
            }
        )
    return out


def _profile_api_key(profile: dict[str, Any]) -> str:
    env = (profile.get("key_env") or "").strip()
    if not env:
        return _env_key()
    key = os.environ.get(env) or ""
    if key:
        return key
    if env == "DEEPSEEK_API_KEY":
        # 历史口径：早期只认 OPENAI_API_KEY
        return os.environ.get("OPENAI_API_KEY") or ""
    return ""


def _apply_model_profile(cfg: dict[str, Any]) -> None:
    """把 active_profile 的地址、模型名与密钥镜像进 ai.*（MiMo 价目在 pricing.py 按模型取）。"""
    prof = active_model_profile(cfg)
    ai = cfg["ai"]
    ai["base_url"] = (prof.get("base_url") or DEFAULTS["ai"]["base_url"]).strip()
    ai["model"] = (prof.get("model") or "").strip()
    ai["api_key"] = _profile_api_key(prof)


def load() -> dict[str, Any]:
    global _cache
    with _lock:
        if _cache is not None:
            return _cache
        _load_dotenv()
        cfg = copy.deepcopy(DEFAULTS)
        stored: dict[str, Any] = {}
        if CONFIG_PATH.exists():
            try:
                parsed = json.loads(CONFIG_PATH.read_text("utf-8"))
                if isinstance(parsed, dict):
                    stored = parsed
                    cfg = _deep_merge(cfg, stored)
            except Exception:
                stored = {}
        cfg = _migrate(cfg)
        ai_stored = stored.get("ai") if isinstance(stored.get("ai"), dict) else {}
        # 只有确实读过一份旧配置、且它还没有分档，才需要把全局地址/模型名归给某一档
        migrated = _apply_env(
            cfg, legacy_profiles=bool(stored) and not isinstance(ai_stored.get("profiles"), list)
        )
        _apply_model_profile(cfg)
        if migrated:
            _persist(cfg)
        _cache = cfg
        return _cache


def _persist(cfg: dict[str, Any]) -> None:
    out = copy.deepcopy(cfg)
    out["ai"]["api_key"] = ""  # 密钥只写 .env
    CONFIG_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2), "utf-8")


def _unmask_secrets(patch: dict[str, Any], cfg: dict[str, Any]) -> None:
    """脱敏串不是新凭据：控制台把整份配置原样存回来时，填回上次的真值，
    并把 public() 附带的 configured 标记剔掉，别把它写进 data/config.json。"""
    srv = patch.get("server")
    if isinstance(srv, dict):
        srv.pop("configured", None)
        if str(srv.get("access_token") or "").startswith("•"):
            srv["access_token"] = str((cfg.get("server") or {}).get("access_token") or "")
    prev = {str(a.get("id")): a for a in cfg.get("accounts") or [] if isinstance(a, dict)}
    for acc in patch.get("accounts") or []:
        if not isinstance(acc, dict):
            continue
        wh = acc.get("webhook")
        if not isinstance(wh, dict):
            continue
        wh.pop("configured", None)
        if str(wh.get("token") or "").startswith("•"):
            old = (prev.get(str(acc.get("id"))) or {}).get("webhook") or {}
            wh["token"] = str(old.get("token") or "")


def save(patch: dict[str, Any]) -> dict[str, Any]:
    """合并保存。

    模型字段按档落盘：``patch.ai`` 里的 ``base_url`` / ``model`` / ``api_key`` 作用于
    ``active_profile`` 指定的那一档（没指定就是当前档），空 api_key 表示不改密钥，
    密钥只写该档对应的 .env 键，不进 config.json。只发 ``active_profile`` 就是纯切档。
    """
    global _cache
    with _lock:
        patch = copy.deepcopy(patch)
        cfg = copy.deepcopy(load())
        ai = patch.get("ai")
        if isinstance(ai, dict):
            items = _ensure_profiles(cfg)
            switch = (ai.get("active_profile") or "").strip()
            known = any(p["id"] == switch for p in items)
            if switch and not known:
                ai.pop("active_profile")
            target = next(
                (p for p in items if p["id"] == (switch if known else cfg["ai"].get("active_profile"))),
                items[0] if items else {},
            )
            if "api_key" in ai:
                new_key = (ai.pop("api_key") or "").strip()
                if new_key and not new_key.startswith("•") and target:
                    env_name = target.get("key_env") or "DEEPSEEK_API_KEY"
                    _write_env_key(env_name, new_key)
                    os.environ[env_name] = new_key
            upd = {}
            for field in ("base_url", "model"):
                if isinstance(ai.get(field), str) and ai[field].strip():
                    upd[field] = ai[field].strip()
                    ai.pop(field)
            if upd and target:
                target.update(upd)
            if known:
                cfg["ai"]["active_profile"] = switch
        _unmask_secrets(patch, cfg)
        cfg = _deep_merge(cfg, patch)
        if not any(a["id"] == cfg.get("active_account") for a in cfg["accounts"]):
            cfg["active_account"] = cfg["accounts"][0]["id"]
        _apply_model_profile(cfg)
        _persist(cfg)
        _cache = cfg
        return cfg


def accounts() -> list[dict[str, Any]]:
    return copy.deepcopy(load()["accounts"])


def account(account_id: str) -> dict[str, Any] | None:
    for a in load()["accounts"]:
        if a["id"] == account_id:
            return copy.deepcopy(a)
    return None


def active_id() -> str:
    cfg = load()
    aid = cfg.get("active_account")
    if any(a["id"] == aid for a in cfg["accounts"]):
        return aid
    return cfg["accounts"][0]["id"]


def set_active(account_id: str) -> bool:
    if not account(account_id):
        return False
    save({"active_account": account_id})
    return True


def upsert_account(payload: dict[str, Any]) -> dict[str, Any]:
    """新增或更新账号，返回保存后的账号。"""
    global _cache
    with _lock:
        cfg = copy.deepcopy(load())
        data = copy.deepcopy(payload)
        aid = data.get("id") or f"acc_{uuid.uuid4().hex[:8]}"
        data["id"] = aid
        base = copy.deepcopy(DEFAULT_ACCOUNT)
        for i, a in enumerate(cfg["accounts"]):
            if a["id"] == aid:
                cfg["accounts"][i] = _deep_merge(a, data)
                break
        else:
            cfg["accounts"].append(_deep_merge(base, data))
        _persist(cfg)
        _cache = cfg
        return account(aid) or {}


def delete_account(account_id: str) -> bool:
    global _cache
    with _lock:
        cfg = copy.deepcopy(load())
        if len(cfg["accounts"]) <= 1:
            return False
        cfg["accounts"] = [a for a in cfg["accounts"] if a["id"] != account_id]
        if cfg.get("active_account") == account_id:
            cfg["active_account"] = cfg["accounts"][0]["id"]
        _persist(cfg)
        _cache = cfg
        return True


def public(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """返回给前端的配置，密钥脱敏（各档只告诉前端有没有填）。"""
    cfg = copy.deepcopy(cfg or load())
    key = cfg["ai"].get("api_key") or ""
    cfg["ai"]["api_key"] = ("•" * 8 + key[-4:]) if key else ""
    cfg["ai"]["configured"] = bool(key)
    cfg["ai"]["profiles"] = model_profiles(cfg)
    if not cfg["ai"]["active_profile"]:
        cfg["ai"]["active_profile"] = cfg["ai"]["profiles"][0]["id"]
    cfg["active_account"] = active_id()
    # 令牌与 webhook 凭据同样是凭据：只告诉前端有没有填、后 4 位是什么
    for acc in cfg.get("accounts") or []:
        wh = acc.get("webhook") or {}
        token = str(wh.get("token") or "")
        wh["configured"] = bool(token)
        wh["token"] = ("•" * 8 + token[-4:]) if token else ""
        acc["webhook"] = wh
    srv = str(cfg["server"].get("access_token") or "")
    cfg["server"]["configured"] = bool(srv)
    cfg["server"]["access_token"] = ("•" * 8 + srv[-4:]) if srv else ""
    return cfg

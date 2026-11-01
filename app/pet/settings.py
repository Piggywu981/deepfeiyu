"""桌宠配置：默认值、夹紧、以及 Helper 需要的 DSH_DAFEIYU_* 环境变量。"""
from __future__ import annotations

from typing import Any

from .. import config
from . import assets, moodlink, protocol

ACTIVITY_LEVELS = ("auto", "quiet", "normal", "lively")
HELPER_STATES = protocol.STATES      # 词表只有 protocol 一份，避免两处各写各的
BUBBLE_MODES = ("always", "hidden", "states")

DEFAULTS: dict[str, Any] = config.DEFAULTS["pet"]
_SCALE = (0.55, 1.4)          # 上游 helper.py 的 clamp 边界
_BUBBLE_SCALE = (0.8, 1.2)


def section(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """整份应用配置里的 pet 段；传 None 时读当前配置。"""
    merged = dict(DEFAULTS)
    merged.update((cfg or config.load()).get("pet") or {})
    return merged


def _pet_section(pet_cfg: dict[str, Any] | None) -> dict[str, Any]:
    """已经取出的 pet 段补默认值。

    section() 收的是整份配置、要往下找一层 "pet"；config_message / env_for_process
    收的已经是 pet 段。两者混用会让现场改动被默认值盖回去（控制台改尺寸无效就是这么来的）。
    """
    merged = dict(DEFAULTS)
    merged.update(pet_cfg or {})
    return merged


def _helper_bubble_mode(mode: str) -> str:
    """控制台叫 states，上游 Helper 只认 custom。"""
    return "custom" if mode == "states" else mode


def _clamp(value: Any, bounds: tuple[float, float], fallback: float) -> float:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return fallback
    return min(bounds[1], max(bounds[0], num))


def normalize(patch: dict[str, Any]) -> dict[str, Any]:
    """只收白名单键；非法值整条丢弃，不悄悄兜一个默认，免得前端以为存成功了。"""
    out: dict[str, Any] = {}
    for key in ("enabled", "reducedMotion", "soundEnabled", "showChatName", "showChatDigest"):
        if key in patch:
            out[key] = bool(patch[key])
    if "scale" in patch:
        out["scale"] = _clamp(patch["scale"], _SCALE, DEFAULTS["scale"])
    if "bubbleScale" in patch:
        out["bubbleScale"] = _clamp(patch["bubbleScale"], _BUBBLE_SCALE, DEFAULTS["bubbleScale"])
    if patch.get("activityLevel") in ACTIVITY_LEVELS:
        out["activityLevel"] = patch["activityLevel"]
    if patch.get("bubbleMode") in BUBBLE_MODES:
        out["bubbleMode"] = patch["bubbleMode"]
    for key in ("bubbleStates", "soundStates"):
        if isinstance(patch.get(key), list):
            out[key] = [s for s in patch[key] if s in HELPER_STATES]
    return out


def save(patch: dict[str, Any]) -> dict[str, Any]:
    return config.save({"pet": normalize(patch)})["pet"]


def moodlink_level(cfg: dict[str, Any], payload: dict[str, Any] | None = None) -> str:
    """auto 档交给心情基线；读不到心情数据时 resolve_level 自己兜 normal。"""
    return moodlink.resolve_level(cfg, payload)


def config_message(pet_cfg: dict[str, Any] | None = None,
                   mood_payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """上游 config 消息：热更尺寸、气泡与声音，不重启进程。"""
    cfg = _pet_section(pet_cfg)
    return {
        "kind": "config",
        "scale": cfg["scale"],
        "bubbleScale": cfg["bubbleScale"],
        "activityLevel": moodlink_level(cfg, mood_payload),
        "reducedMotion": cfg["reducedMotion"],
        "soundEnabled": cfg["soundEnabled"],
        "soundStates": cfg["soundStates"],
        "bubbleMode": _helper_bubble_mode(cfg["bubbleMode"]),
        "bubbleStates": cfg["bubbleStates"],
    }


def env_for_process(pet_cfg: dict[str, Any] | None = None,
                    mood_payload: dict[str, Any] | None = None) -> dict[str, str]:
    """子进程启动种子；运行期改动走 config_message 热更。"""
    cfg = _pet_section(pet_cfg)
    bundle = assets.bundle_dir()
    port = (config.load().get("server") or {}).get("port", 8765)
    return {
        "DSH_DAFEIYU_LAYOUT_PATH": str(bundle / "layout.json"),
        "DSH_DAFEIYU_LOG_FILE": str(bundle / "helper.log"),
        "DSH_DAFEIYU_WEBUI_URL": f"http://127.0.0.1:{port}/",
        "DSH_DAFEIYU_OPEN_LABEL": "打开 Deep肥鱼控制台",
        "DSH_DAFEIYU_SCALE": str(cfg["scale"]),
        "DSH_DAFEIYU_BUBBLE_SCALE": str(cfg["bubbleScale"]),
        "DSH_DAFEIYU_REDUCED_MOTION": "1" if cfg["reducedMotion"] else "0",
        "DSH_DAFEIYU_SOUND_ENABLED": "1" if cfg["soundEnabled"] else "0",
        "DSH_DAFEIYU_SOUND_STATES": ",".join(cfg["soundStates"]),
        "DSH_DAFEIYU_ACTIVITY_LEVEL": moodlink_level(cfg, mood_payload),
        "DSH_DAFEIYU_BUBBLE_MODE": _helper_bubble_mode(cfg["bubbleMode"]),
        "DSH_DAFEIYU_BUBBLE_STATES": ",".join(cfg["bubbleStates"]),
    }

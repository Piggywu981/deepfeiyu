"""可选功能依赖注册表：装什么、怎么探测、界面上怎么说明。

包名、requirements 文件名与 pip 参数全部写死在这里，前端只能传 feature id。
这是有意的：即使 /api/deps 只允许本机访问，也不该开一个"任意 pip install"的口子。
"""
from __future__ import annotations

import importlib.util
import os
from dataclasses import dataclass
from typing import Any

from ..bili import setup as bili_setup
from ..pet import install as pet_install


@dataclass(frozen=True)
class Feature:
    """一个可选功能的依赖说明。

    modules 里的 (导入名, 包名标签)：探测用 find_spec 的导入名，报给用户看的是
    pip 包名（如 win32api → pywin32），前端可以照着标签直接装。
    """

    id: str
    label: str
    desc: str
    kind: str                                    # "pip" | "delegated"
    windows_only: bool = False
    modules: tuple[tuple[str, str], ...] = ()
    requirements: str = ""                       # pip 型：仓库根下的 requirements 文件名
    package: str = ""                            # pip 型：固定包名（wcferry 无 requirements）
    delegate: str = ""                           # 委托型："pet" | "bili"
    log_source: str = ""                         # 委托型：日志来源说明，读取时加在最前面


FEATURES: tuple[Feature, ...] = (
    Feature(
        id="core",
        label="核心依赖",
        desc="控制台启动必需：FastAPI、uvicorn、httpx、Pydantic、multipart、Pillow",
        kind="pip",
        modules=(
            ("fastapi", "fastapi"),
            ("uvicorn", "uvicorn"),
            ("httpx", "httpx"),
            ("pydantic", "pydantic"),
            ("multipart", "python-multipart"),
            ("PIL", "Pillow"),
        ),
        requirements="requirements.txt",
    ),
    Feature(
        id="weixin4",
        label="微信 4.x 通道",
        desc="后台读数据库收消息 + UIA 前台发送，微信 4.x 客户端需另装",
        kind="pip",
        windows_only=True,
        modules=(
            ("wechatauto", "wechatauto-replica"),
            ("uiautomation", "uiautomation"),
            ("cryptography", "cryptography"),
            ("zstandard", "zstandard"),
            ("pyperclip", "pyperclip"),
            ("colorama", "colorama"),
        ),
        requirements="requirements-weixin4.txt",
    ),
    Feature(
        id="moments",
        label="朋友圈",
        desc="朋友圈收发、点赞评论与图片工具",
        kind="pip",
        windows_only=True,
        modules=(
            ("win32api", "pywin32"),
            ("numpy", "numpy"),
            ("cv2", "opencv-python"),
            ("pyautogui", "pyautogui"),
            ("winsdk", "winsdk"),
            ("psutil", "psutil"),
        ),
        requirements="requirements-moments.txt",
    ),
    Feature(
        id="pet",
        label="桌面宠物",
        desc="PySide6 桌宠子进程，素材装配在「桌宠」页一键准备",
        kind="delegated",
        windows_only=True,
        modules=(("PySide6", "PySide6"),),
        delegate="pet",
        log_source="桌宠一键准备日志 data/pet/install.log",
    ),
    Feature(
        id="wcferry",
        label="WCFerry（微信 3.9.x）",
        desc="微信 3.9.x 本地 Hook 通道，需配合 3.9.10.27 客户端",
        kind="pip",
        windows_only=True,
        modules=(("wcferry", "wcferry"),),
        package="wcferry",
    ),
    Feature(
        id="bili",
        label="B 站 MCP",
        desc="独立 venv 的只读 B 站 MCP",
        kind="delegated",
        delegate="bili",
        log_source="B 站安装日志 data/bili/setup.log",
    ),
)

_BY_ID: dict[str, Feature] = {feature.id: feature for feature in FEATURES}


def get(feature_id: str) -> Feature | None:
    """按 id 取功能；只认注册表里的常量，绝不把输入拼进命令。"""
    if not isinstance(feature_id, str):
        return None
    return _BY_ID.get(feature_id)


def label_of(feature_id: str) -> str:
    feature = get(feature_id)
    return feature.label if feature else str(feature_id)


def supported(feature: Feature) -> bool:
    """平台闸门：Windows 专用功能在别的系统上判不支持，命令根本不执行。"""
    return os.name == "nt" if feature.windows_only else True


def module_available(module: str) -> bool:
    """find_spec 只查不导入：探测可选依赖时不能把包真的 import 进来。"""
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def missing_modules(feature: Feature) -> list[str]:
    """缺失模块对应的包名标签；pip 结束后 installer 用它复检是否真的装上了。"""
    return [label for module, label in feature.modules if not module_available(module)]


def _pip_note(feature: Feature) -> str:
    # 核心依赖装完不会热生效：进程里已经 import 过的模块还是旧版本，必须重启控制台
    if feature.id == "core":
        return "改动核心依赖后需要重启控制台"
    return ""


def _safe_status(module: Any) -> dict[str, Any]:
    """委托型状态读失败按未就绪显示：整个状态接口不该因为一个子模块异常而 500。"""
    try:
        data = module.status()
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def describe(feature: Feature) -> dict[str, Any]:
    """一个功能当前的就绪情况（不含安装 phase，phase 由 installer 读各自状态机）。"""
    out: dict[str, Any] = {
        "id": feature.id,
        "label": feature.label,
        "desc": feature.desc,
        "kind": feature.kind,
        "platform": "windows" if feature.windows_only else "any",
        "supported": supported(feature),
        "installed": False,
        "partial": False,
        "missing": [],
        "present": 0,
        "total": len(feature.modules),
        "note": "",
    }
    if feature.kind == "pip":
        missing = missing_modules(feature)
        out["missing"] = missing
        out["present"] = out["total"] - len(missing)
        out["installed"] = out["total"] > 0 and not missing
        out["partial"] = 0 < out["present"] < out["total"]
        out["note"] = _pip_note(feature)
    elif feature.delegate == "pet":
        status = _safe_status(pet_install)
        deps_ready = bool(status.get("depsReady"))
        assets_ready = bool(status.get("assetsReady"))
        out["installed"] = deps_ready
        out["present"] = 1 if deps_ready else 0
        out["total"] = 1
        out["missing"] = [] if deps_ready else ["PySide6"]
        out["note"] = "" if assets_ready else "素材未装配时到「桌宠」页点一键准备"
        out["assetsReady"] = assets_ready
    elif feature.delegate == "bili":
        status = _safe_status(bili_setup)
        installed = bool(status.get("installed"))
        out["installed"] = installed
        # B 站没有可探测的模块：判定完全交给既有 setup 流程的 installed 字段
        out["total"] = 0
        out["note"] = "" if installed else "日志在 data/bili/setup.log"
    return out

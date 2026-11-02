"""可选功能依赖 API：就绪状态、一键安装、增量日志。

三个端点都只允许本机来源：POST 会真的执行 pip 或既有一键流程，不能给远端调。
安装动作只接受 feature id，命令的包名与参数由 features 注册表写死。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import config
from . import features, installer

router = APIRouter(prefix="/api/deps", tags=["deps"])


class InstallBody(BaseModel):
    # 只收 id；前端多传 package / pip_args 之类字段会被忽略，进不了命令
    feature: str


def _require_local(request: Request) -> None:
    host = request.client.host if request.client else ""
    if host not in config.LOCAL_HOSTS:
        raise HTTPException(403, "依赖安装只允许在本机控制台执行")


def _feature_payload(feature: features.Feature) -> dict[str, Any]:
    # 描述（探测结果）与安装状态（各自状态机）分开算，避免两处重复读状态文件
    return {**features.describe(feature), **installer.phase_of(feature)}


@router.get("/state")
def get_state(request: Request) -> dict[str, Any]:
    _require_local(request)
    return {
        "ok": True,
        "installing": installer.installing_feature(),
        "features": [_feature_payload(feature) for feature in features.FEATURES],
    }


@router.post("/install")
async def post_install(request: Request, body: InstallBody) -> dict[str, Any]:
    _require_local(request)
    feature_id = str(body.feature or "").strip()
    feature = features.get(feature_id)
    if feature is None:
        raise HTTPException(400, f"未知功能：{feature_id}")
    if not features.supported(feature):
        return {"ok": False, "started": False, "feature": feature.id,
                "error": "该功能仅支持 Windows", "log_offset": 0}
    return await installer.start_install(feature.id)


@router.get("/install/log")
async def get_install_log(
    request: Request, feature: str | None = None, offset: int = 0
) -> dict[str, Any]:
    _require_local(request)
    feature_id = str(feature or "").strip()
    if feature_id:
        if features.get(feature_id) is None:
            raise HTTPException(400, f"未知功能：{feature_id}")
    # 负 offset 故意不夹成 0：按 pet 的日志语义，负数表示"从尾部重读"（reset）
    return await installer.log_since(feature_id or None, int(offset or 0))

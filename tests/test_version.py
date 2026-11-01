"""版本号有唯一来源，且各展示位都读它。"""
from pathlib import Path

from app import __version__


def test_version_shape():
    assert __version__.count(".") == 2 and __version__[0].isdigit()


def test_fastapi_reports_version():
    from app.main import app

    assert app.version == __version__


def test_health_reports_version():
    from fastapi.testclient import TestClient

    from app.main import app

    # 不进 with：走 lifespan 会拉起后台线程，它们在本文件后续用例的
    # 收尾阶段往已关闭的捕获流里写日志，表现为 I/O operation on closed file
    res = TestClient(app).get("/api/health")
    assert res.json()["version"] == __version__


def test_whale_widget_version_uses_real_number():
    from app import whale

    # 两个键都会被 /dsh-whale/balance.json 原样外显：机器读 version，人读 versionLabel
    assert whale.VERSION["version"] == __version__
    assert whale.VERSION["versionLabel"] == f"Deep肥鱼 v{__version__}"


def test_frontend_reads_server_version():
    js = Path("app/static/app.js").read_text(encoding="utf-8")
    html = Path("app/static/index.html").read_text(encoding="utf-8")
    assert "非 git 安装" not in js          # 会让 zip 安装用户以为装坏了
    assert "/api/health" in js              # 版本从 health 取，不新加接口
    assert 'id="verLabel"' in html

"""表情包预览墙：标签规范化、覆盖写、手动编辑、AI 重打标与多选批处理接口。"""
import base64
import hashlib
import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from app import db, main


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


MD5_A = "a" * 32


def test_normalize_tags_splits_and_dedupes():
    assert db.normalize_tags("无语，加班、摸鱼 猫") == ["无语", "加班", "摸鱼", "猫"]
    assert db.normalize_tags("无语, 无语 ,  加班") == ["无语", "加班"]
    assert db.normalize_tags("") == []
    assert db.normalize_tags(None) == []


def test_normalize_tags_caps_length_and_count():
    assert db.normalize_tags("x" * 30) == ["x" * 20]
    assert db.normalize_tags(",".join(str(i) for i in range(12))) == [str(i) for i in range(8)]


def test_set_emoji_tags_overwrites(temp_db):
    row = db.add_emoji("acc_a", "https://cdn/a", md5=MD5_A)
    db.update_emoji_tags("acc_a", row["id"], "旧标签", "旧描述")
    assert db.set_emoji_tags("acc_a", row["id"], "无语,加班", "熊猫头") is True
    fresh = db.get_emoji("acc_a", row["id"])
    assert fresh["tags"] == "无语,加班"
    assert fresh["description"] == "熊猫头"
    # 允许清空：回到「未打标」
    assert db.set_emoji_tags("acc_a", row["id"], "", "") is True
    assert db.get_emoji("acc_a", row["id"])["tags"] == ""


def test_set_emoji_tags_scoped_to_account(temp_db):
    row = db.add_emoji("acc_a", "https://cdn/a", md5=MD5_A)
    assert db.set_emoji_tags("acc_b", row["id"], "越权") is False
    assert db.get_emoji("acc_a", row["id"])["tags"] == ""
    assert db.set_emoji_tags("acc_a", 99999, "x") is False


class FakeSession:
    def __init__(self, llm=None) -> None:
        self.llm = llm


@pytest.fixture()
def api(temp_db, monkeypatch):
    monkeypatch.setattr(main, "acc_id", lambda request: "acc_a")
    monkeypatch.setattr(main, "session_of", lambda request: FakeSession())
    return TestClient(main.app)  # 不用 with，避免触发 lifespan 启动 Hub


def test_put_emoji_updates_tags_and_description(api):
    row = db.add_emoji("acc_a", "https://cdn/a", md5=MD5_A)
    db.update_emoji_tags("acc_a", row["id"], "旧", "旧描述")
    resp = api.put(f"/api/emojis/{row['id']}", json={"tags": "无语，加班", "description": "新描述"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["emoji"]["tags"] == "无语,加班"  # 中文逗号被规范化
    assert body["emoji"]["description"] == "新描述"


def test_put_emoji_empty_tags_clears(api):
    row = db.add_emoji("acc_a", "https://cdn/a", md5=MD5_A)
    db.update_emoji_tags("acc_a", row["id"], "旧", "")
    resp = api.put(f"/api/emojis/{row['id']}", json={"tags": "", "description": ""})
    assert resp.status_code == 200
    assert resp.json()["emoji"]["tags"] == ""


def test_put_emoji_404_for_missing_or_foreign(api):
    assert api.put("/api/emojis/999", json={"tags": "x"}).status_code == 404
    foreign = db.add_emoji("acc_b", "https://cdn/b", md5="b" * 32)
    assert api.put(f"/api/emojis/{foreign['id']}", json={"tags": "x"}).status_code == 404


PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)
# 导入链路按现有规则拒收 <512B 的占位图/坏块，测试图垫够体积
PNG_BYTES = PNG_1PX + b"\0" * 1024
GIF_BYTES = b"GIF89a" + b"\0" * 1024
PNG_MD5 = hashlib.md5(PNG_BYTES).hexdigest()


class FakeLLM:
    configured = True

    def __init__(self, reply: str) -> None:
        self.reply = reply

    async def stream(self, messages, **kwargs):
        yield {"type": "text", "text": self.reply}


@pytest.fixture()
def emoji_cache(tmp_path, monkeypatch):
    """把表情缓存目录指到临时目录，避免测试写进 data/media/emojis。"""
    path = tmp_path / "emojis"
    monkeypatch.setattr("app.emoji.EMOJI_DIR", path)
    return path


def _seed_cache(url: str, md5: str) -> None:
    from app.emoji import cache_path

    path = cache_path(url, md5)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(PNG_BYTES)


def test_retag_success_overwrites(api, emoji_cache, monkeypatch):
    row = db.add_emoji("acc_a", "https://cdn/a", md5=MD5_A)
    db.update_emoji_tags("acc_a", row["id"], "旧标签", "旧描述")
    _seed_cache(row["url"], MD5_A)
    monkeypatch.setattr(
        main, "session_of",
        lambda request: FakeSession(FakeLLM('{"tags": ["无语", "加班"], "description": "熊猫头"}')),
    )
    resp = api.post(f"/api/emojis/{row['id']}/retag")
    assert resp.status_code == 200
    body = resp.json()
    assert body["emoji"]["tags"] == "无语,加班"
    assert body["emoji"]["description"] == "熊猫头"


def test_retag_rejects_unconfigured_llm(api, monkeypatch):
    class NotConfigured:
        configured = False

    monkeypatch.setattr(main, "session_of", lambda request: FakeSession(NotConfigured()))
    row = db.add_emoji("acc_a", "https://cdn/a", md5=MD5_A)
    resp = api.post(f"/api/emojis/{row['id']}/retag")
    assert resp.status_code == 400


def test_retag_rejects_missing_cache(api, monkeypatch):
    monkeypatch.setattr(main, "session_of", lambda request: FakeSession(FakeLLM('{"tags": ["x"]}')))
    row = db.add_emoji("acc_a", "https://cdn/a", md5=MD5_A)
    resp = api.post(f"/api/emojis/{row['id']}/retag")
    assert resp.status_code == 400


def test_retag_502_when_model_returns_no_tags(api, emoji_cache, monkeypatch):
    row = db.add_emoji("acc_a", "https://cdn/a", md5=MD5_A)
    _seed_cache(row["url"], MD5_A)
    monkeypatch.setattr(main, "session_of", lambda request: FakeSession(FakeLLM("我不会打标签")))
    resp = api.post(f"/api/emojis/{row['id']}/retag")
    assert resp.status_code == 502
    # 失败时旧标签保留
    assert db.get_emoji("acc_a", row["id"])["tags"] == ""


# ------------------------------------------------------------- 多选批处理

def test_batch_delete_removes_only_own(api):
    a = db.add_emoji("acc_a", "https://cdn/a", md5=MD5_A)
    b = db.add_emoji("acc_a", "https://cdn/b", md5="b" * 32)
    foreign = db.add_emoji("acc_b", "https://cdn/c", md5="c" * 32)
    resp = api.post(
        "/api/emojis/batch-delete",
        json={"ids": [a["id"], b["id"], foreign["id"], 99999]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["deleted"] == 2  # 锁定行一条没删，跳过数单独报
    assert body["skipped_locked"] == 0
    assert body["ok"] is True
    assert db.get_emoji("acc_a", a["id"]) is None
    assert db.get_emoji("acc_a", b["id"]) is None
    assert db.get_emoji("acc_b", foreign["id"]) is not None  # 别人账号的动不了


def test_batch_delete_empty_ids_noop(api):
    row = db.add_emoji("acc_a", "https://cdn/a", md5=MD5_A)
    resp = api.post("/api/emojis/batch-delete", json={"ids": []})
    assert resp.status_code == 200
    assert resp.json()["deleted"] == 0
    assert db.get_emoji("acc_a", row["id"]) is not None


def test_batch_lock_and_unlock(api):
    a = db.add_emoji("acc_a", "https://cdn/a", md5=MD5_A)
    b = db.add_emoji("acc_a", "https://cdn/b", md5="b" * 32)
    resp = api.post(
        "/api/emojis/batch-lock", json={"ids": [a["id"], b["id"]], "locked": True}
    )
    assert resp.status_code == 200
    assert resp.json()["updated"] == 2
    assert resp.json()["locked"] is True
    assert db.get_emoji("acc_a", a["id"])["locked"] == 1
    assert db.get_emoji("acc_a", b["id"])["locked"] == 1
    # 锁定后标签修改照旧被拦
    assert api.put(
        f"/api/emojis/{a['id']}", json={"tags": "新", "description": ""}
    ).status_code == 409
    unlocked = api.post(
        "/api/emojis/batch-lock", json={"ids": [a["id"], b["id"]], "locked": False}
    )
    assert unlocked.status_code == 200
    assert unlocked.json()["updated"] == 2
    assert db.get_emoji("acc_a", a["id"])["locked"] == 0


def test_batch_lock_skips_foreign_and_missing(api):
    a = db.add_emoji("acc_a", "https://cdn/a", md5=MD5_A)
    foreign = db.add_emoji("acc_b", "https://cdn/b", md5="b" * 32)
    resp = api.post(
        "/api/emojis/batch-lock", json={"ids": [a["id"], foreign["id"], 99999], "locked": True}
    )
    assert resp.status_code == 200
    assert resp.json()["updated"] == 1
    assert db.get_emoji("acc_a", a["id"])["locked"] == 1
    assert db.get_emoji("acc_b", foreign["id"])["locked"] == 0


def test_batch_lock_empty_ids_noop(api):
    row = db.add_emoji("acc_a", "https://cdn/a", md5=MD5_A)
    resp = api.post("/api/emojis/batch-lock", json={"ids": [], "locked": True})
    assert resp.status_code == 200
    assert resp.json()["updated"] == 0
    assert db.get_emoji("acc_a", row["id"])["locked"] == 0


def test_batch_zip_packs_cached_and_skips_broken(api, emoji_cache, monkeypatch):
    import app.emoji as emoji_mod

    good = db.add_emoji("acc_a", "https://cdn/a", md5=MD5_A)
    _seed_cache(good["url"], MD5_A)
    bad = db.add_emoji("acc_a", "https://cdn/bad", md5="d" * 32)  # 没有本地缓存

    real_fetch = emoji_mod.fetch_emoji

    def fake_fetch(url, md5=""):
        if "bad" in url:
            raise emoji_mod.EmojiURLError("网络不可达")
        return real_fetch(url, md5)

    monkeypatch.setattr(emoji_mod, "fetch_emoji", fake_fetch)
    resp = api.post("/api/emojis/batch-zip", json={"ids": [good["id"], bad["id"]]})
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    assert resp.headers["x-emoji-skipped"] == "1"
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    assert sorted(zf.namelist()) == sorted(
        [f"{good['id']}_{'a' * 24}.gif", "manifest.json"]
    )


def test_batch_zip_502_when_all_unfetchable(api, emoji_cache, monkeypatch):
    import app.emoji as emoji_mod

    row = db.add_emoji("acc_a", "https://cdn/bad", md5=MD5_A)

    def fail_fetch(url, md5=""):
        raise emoji_mod.EmojiURLError("网络不可达")

    monkeypatch.setattr(emoji_mod, "fetch_emoji", fail_fetch)
    assert api.post("/api/emojis/batch-zip", json={"ids": [row["id"]]}).status_code == 502
    # 选中的 id 都不存在时直接 404
    assert api.post("/api/emojis/batch-zip", json={"ids": [12345]}).status_code == 404


# ------------------------------------------------------- 批量导入导出（带标签）

def _zip_bytes(entries: dict[str, bytes], items: list[dict] | None = None) -> bytes:
    """构造导入用 zip；items 为 None 时不写 manifest（模拟旧版导出）。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
        if items is not None:
            zf.writestr(
                "manifest.json",
                json.dumps(
                    {"format": "deepfeiyu-emojis", "version": 1, "items": items},
                    ensure_ascii=False,
                ),
            )
    return buf.getvalue()


def _manifest_of(blob: bytes) -> dict:
    zf = zipfile.ZipFile(io.BytesIO(blob))
    return json.loads(zf.read("manifest.json").decode("utf-8"))


def test_import_emoji_db_adds_merges_and_is_idempotent(temp_db):
    assert db.import_emoji("acc_a", MD5_A, "无语,加班", "熊猫头", 120, 80) == "added"
    row = db.get_emoji_by_md5("acc_a", MD5_A)
    assert row["url"] == f"local://{MD5_A}"
    assert row["tags"] == "无语,加班"
    assert row["description"] == "熊猫头"
    assert (row["width"], row["height"]) == (120, 80)

    assert db.import_emoji("acc_a", MD5_A, "加班,新", "新描述") == "updated"
    row = db.get_emoji_by_md5("acc_a", MD5_A)
    assert row["tags"] == "无语,加班,新"  # 原标签在前，取并集
    assert row["description"] == "熊猫头"  # 已有描述不被覆盖

    assert db.import_emoji("acc_a", MD5_A, "加班,新", "新描述") == "unchanged"


def test_import_emoji_db_caps_merged_tags(temp_db):
    db.import_emoji("acc_a", MD5_A, ",".join(f"旧{i}" for i in range(6)))
    db.import_emoji("acc_a", MD5_A, ",".join(f"新{i}" for i in range(6)))
    tags = db.get_emoji_by_md5("acc_a", MD5_A)["tags"].split(",")
    assert len(tags) == 8
    assert tags[:6] == [f"旧{i}" for i in range(6)]


def test_export_manifest_carries_tags_and_skips_missing(api, emoji_cache):
    good = db.add_emoji("acc_a", "https://cdn/a", md5=MD5_A)
    db.set_emoji_tags("acc_a", good["id"], "无语,加班", "熊猫头")
    _seed_cache(good["url"], MD5_A)
    bad = db.add_emoji("acc_a", "https://cdn/bad", md5="d" * 32)  # 没有本地缓存

    resp = api.post("/api/emojis/batch-zip", json={"ids": [good["id"], bad["id"]]})
    assert resp.status_code == 200
    assert resp.headers["x-emoji-packed"] == "1"
    assert resp.headers["x-emoji-skipped"] == "1"
    manifest = _manifest_of(resp.content)
    assert manifest["format"] == "deepfeiyu-emojis"
    assert manifest["version"] == 1
    assert len(manifest["items"]) == 1
    item = manifest["items"][0]
    assert item["file"] == f"{good['id']}_{'a' * 24}.gif"
    assert item["md5"] == MD5_A
    assert item["tags"] == ["无语", "加班"]
    assert item["description"] == "熊猫头"


def test_export_all_only_current_account(api, emoji_cache):
    a = db.add_emoji("acc_a", "https://cdn/a", md5=MD5_A)
    b = db.add_emoji("acc_a", "https://cdn/b", md5="b" * 32)
    foreign = db.add_emoji("acc_b", "https://cdn/c", md5="c" * 32)
    for row in (a, b, foreign):
        _seed_cache(row["url"], row["md5"])

    resp = api.post("/api/emojis/batch-zip", json={"all": True})
    assert resp.status_code == 200
    names = zipfile.ZipFile(io.BytesIO(resp.content)).namelist()
    assert f"{a['id']}_{MD5_A[:24]}.gif" in names
    assert f"{b['id']}_{('b' * 32)[:24]}.gif" in names
    assert all(not name.startswith(f"{foreign['id']}_") for name in names)
    assert len(_manifest_of(resp.content)["items"]) == 2


def test_export_import_roundtrip_restores_tags(api, emoji_cache):
    row = db.add_emoji("acc_a", "https://cdn/a", md5=PNG_MD5)
    db.set_emoji_tags("acc_a", row["id"], "无语,加班", "熊猫头")
    _seed_cache(row["url"], PNG_MD5)
    blob = api.post("/api/emojis/batch-zip", json={"ids": [row["id"]]}).content

    assert db.remove_emoji("acc_a", row["id"]) is True
    resp = api.post(
        "/api/emojis/import",
        files=[("files", ("emojis.zip", blob, "application/zip"))],
    )
    assert resp.status_code == 200
    body = resp.json()
    assert (body["added"], body["updated"], body["skipped"]) == (1, 0, 0)

    rows = db.list_emojis("acc_a")
    assert len(rows) == 1
    restored = rows[0]
    assert restored["md5"] == PNG_MD5
    assert restored["url"] == f"local://{PNG_MD5}"
    assert restored["tags"] == "无语,加班"
    assert restored["description"] == "熊猫头"
    assert (emoji_cache / f"{PNG_MD5}.png").is_file()  # 按内容识别成 PNG 落缓存


def test_import_into_another_account(api, emoji_cache, monkeypatch):
    row = db.add_emoji("acc_a", "https://cdn/a", md5=PNG_MD5)
    db.set_emoji_tags("acc_a", row["id"], "无语", "")
    _seed_cache(row["url"], PNG_MD5)
    blob = api.post("/api/emojis/batch-zip", json={"ids": [row["id"]]}).content

    monkeypatch.setattr(main, "acc_id", lambda request: "acc_b")
    resp = api.post(
        "/api/emojis/import",
        files=[("files", ("emojis.zip", blob, "application/zip"))],
    )
    assert resp.status_code == 200
    assert resp.json()["added"] == 1
    assert db.get_emoji("acc_a", row["id"]) is not None  # 原账号不受影响
    imported = db.list_emojis("acc_b")
    assert len(imported) == 1 and imported[0]["tags"] == "无语"


def test_import_merges_tags_without_overwriting_description(api, emoji_cache):
    row = db.add_emoji("acc_a", "https://cdn/a", md5=PNG_MD5)
    db.set_emoji_tags("acc_a", row["id"], "旧", "旧描述")
    blob = _zip_bytes(
        {"x.png": PNG_BYTES},
        [{"file": "x.png", "md5": PNG_MD5, "tags": ["新", "旧", "新"], "description": "新描述"}],
    )

    resp = api.post(
        "/api/emojis/import",
        files=[("files", ("x.zip", blob, "application/zip"))],
    )
    assert resp.status_code == 200
    assert resp.json()["updated"] == 1
    rows = db.list_emojis("acc_a")
    assert len(rows) == 1
    assert rows[0]["tags"] == "旧,新"
    assert rows[0]["description"] == "旧描述"
    assert rows[0]["url"] == "https://cdn/a"  # 不覆盖已有行的链接


def test_import_loose_images_applies_common_tags(api, emoji_cache):
    files = [
        ("files", ("a.png", PNG_BYTES, "image/png")),
        ("files", ("b.gif", GIF_BYTES, "image/gif")),
    ]
    resp = api.post("/api/emojis/import", files=files, data={"tags": "无语，加班"})
    assert resp.status_code == 200
    assert resp.json()["added"] == 2
    rows = db.list_emojis("acc_a")
    assert len(rows) == 2
    assert {r["tags"] for r in rows} == {"无语,加班"}
    assert {r["url"] for r in rows} == {
        f"local://{hashlib.md5(PNG_BYTES).hexdigest()}",
        f"local://{hashlib.md5(GIF_BYTES).hexdigest()}",
    }
    assert (emoji_cache / f"{hashlib.md5(PNG_BYTES).hexdigest()}.png").is_file()
    assert (emoji_cache / f"{hashlib.md5(GIF_BYTES).hexdigest()}.gif").is_file()


def test_import_old_zip_without_manifest_uses_common_tags(api, emoji_cache):
    blob = _zip_bytes({"old.png": PNG_BYTES})
    resp = api.post(
        "/api/emojis/import",
        files=[("files", ("old.zip", blob, "application/zip"))],
        data={"tags": "摸鱼"},
    )
    assert resp.status_code == 200
    assert resp.json()["added"] == 1
    assert db.list_emojis("acc_a")[0]["tags"] == "摸鱼"


def test_import_skips_bad_inputs_but_keeps_good_ones(api, emoji_cache):
    resp = api.post(
        "/api/emojis/import",
        files=[("files", ("bad.zip", b"not-a-zip", "application/zip"))],
    )
    assert resp.status_code == 400  # 一个都没导进去

    resp = api.post(
        "/api/emojis/import",
        files=[
            ("files", ("bad.zip", b"not-a-zip", "application/zip")),
            ("files", ("ok.png", PNG_BYTES, "image/png")),
        ],
    )
    assert resp.status_code == 200
    body = resp.json()
    assert (body["added"], body["skipped"]) == (1, 1)


def test_import_skips_oversized_file(api, emoji_cache, monkeypatch):
    import app.emoji as emoji_mod

    monkeypatch.setattr(emoji_mod, "IMPORT_MAX_FILE_BYTES", 1024)
    big = PNG_1PX + b"\0" * 2048
    small = PNG_1PX + b"\0" * 600
    resp = api.post(
        "/api/emojis/import",
        files=[
            ("files", ("big.png", big, "image/png")),
            ("files", ("small.png", small, "image/png")),
        ],
    )
    assert resp.status_code == 200
    body = resp.json()
    assert (body["added"], body["skipped"]) == (1, 1)


def test_import_rejects_whole_zip_when_too_many_entries(api, emoji_cache, monkeypatch):
    import app.emoji as emoji_mod

    monkeypatch.setattr(emoji_mod, "IMPORT_MAX_FILES", 1)
    blob = _zip_bytes({"a.png": PNG_BYTES, "b.gif": GIF_BYTES})
    resp = api.post(
        "/api/emojis/import",
        files=[("files", ("many.zip", blob, "application/zip"))],
    )
    assert resp.status_code == 400


def test_import_rejects_too_many_uploaded_files(api, emoji_cache, monkeypatch):
    import app.emoji as emoji_mod

    monkeypatch.setattr(emoji_mod, "IMPORT_MAX_FILES", 1)
    resp = api.post(
        "/api/emojis/import",
        files=[
            ("files", ("a.png", PNG_BYTES, "image/png")),
            ("files", ("b.gif", GIF_BYTES, "image/gif")),
        ],
    )
    assert resp.status_code == 400


def test_import_budget_shared_across_uploaded_zips(api, emoji_cache, monkeypatch):
    """单次请求的 5000 个上限跨上传文件累计，不能每个 zip 各算一份。"""
    import app.emoji as emoji_mod

    monkeypatch.setattr(emoji_mod, "IMPORT_MAX_FILES", 2)
    first = _zip_bytes({"a.png": PNG_BYTES, "b.gif": GIF_BYTES})
    second = _zip_bytes({"c.gif": b"GIF89a" + b"\1" * 1024})
    resp = api.post(
        "/api/emojis/import",
        files=[
            ("files", ("first.zip", first, "application/zip")),
            ("files", ("second.zip", second, "application/zip")),
        ],
    )
    assert resp.status_code == 200
    body = resp.json()
    assert (body["added"], body["skipped"]) == (2, 1)
    assert len(db.list_emojis("acc_a")) == 2


def test_import_zip_manifest_not_counted_in_file_limit(api, emoji_cache, monkeypatch):
    """manifest.json 是导出清单，不是图片，不占 5000 个条目额度。"""
    import app.emoji as emoji_mod

    monkeypatch.setattr(emoji_mod, "IMPORT_MAX_FILES", 1)
    blob = _zip_bytes(
        {"a.png": PNG_BYTES},
        [{"file": "a.png", "tags": ["一条"], "description": ""}],
    )
    resp = api.post(
        "/api/emojis/import",
        files=[("files", ("one.zip", blob, "application/zip"))],
    )
    assert resp.status_code == 200
    assert resp.json()["added"] == 1


def test_import_manifest_clamps_huge_dimensions(api, emoji_cache):
    blob = _zip_bytes(
        {"x.png": PNG_BYTES},
        [{"file": "x.png", "tags": [], "description": "", "width": 10**30, "height": -5}],
    )
    resp = api.post(
        "/api/emojis/import",
        files=[("files", ("dim.zip", blob, "application/zip"))],
    )
    assert resp.status_code == 200
    row = db.list_emojis("acc_a")[0]
    assert row["width"] == 20000  # 荒谬尺寸夹到上限，不能炸整包
    assert row["height"] == 0


def test_import_skips_oversized_zip_entry(api, emoji_cache, monkeypatch):
    import app.emoji as emoji_mod

    monkeypatch.setattr(emoji_mod, "IMPORT_MAX_FILE_BYTES", 1024)
    blob = _zip_bytes(
        {
            "big.png": PNG_1PX + b"\0" * 2048,
            "ok.png": PNG_1PX + b"\0" * 600,
        }
    )
    resp = api.post(
        "/api/emojis/import",
        files=[("files", ("mix.zip", blob, "application/zip"))],
    )
    assert resp.status_code == 200
    body = resp.json()
    assert (body["added"], body["skipped"]) == (1, 1)


def test_import_zip_entry_names_cannot_escape_cache(api, emoji_cache):
    blob = _zip_bytes({"../../evil.png": PNG_BYTES})
    resp = api.post(
        "/api/emojis/import",
        files=[("files", ("evil.zip", blob, "application/zip"))],
    )
    assert resp.status_code == 200
    assert not (emoji_cache.parent / "evil.png").exists()
    assert not (emoji_cache.parent.parent / "evil.png").exists()
    assert len(list(emoji_cache.glob("*.png"))) == 1


def test_import_budget_shared_bytes(api, emoji_cache, monkeypatch):
    """1GB 解压总量同样跨上传文件累计。"""
    import app.emoji as emoji_mod

    monkeypatch.setattr(emoji_mod, "IMPORT_MAX_TOTAL_BYTES", 1500)
    resp = api.post(
        "/api/emojis/import",
        files=[
            ("files", ("a.png", PNG_BYTES, "image/png")),
            ("files", ("b.gif", GIF_BYTES, "image/gif")),
        ],
    )
    assert resp.status_code == 200
    body = resp.json()
    assert (body["added"], body["skipped"]) == (1, 1)
    assert len(db.list_emojis("acc_a")) == 1


def test_import_manifest_tags_as_string(api, emoji_cache):
    blob = _zip_bytes(
        {"x.png": PNG_BYTES},
        [{"file": "x.png", "tags": "无语，加班", "description": ""}],
    )
    resp = api.post(
        "/api/emojis/import",
        files=[("files", ("str-tags.zip", blob, "application/zip"))],
    )
    assert resp.status_code == 200
    assert db.list_emojis("acc_a")[0]["tags"] == "无语,加班"


def test_import_fills_empty_description(api, emoji_cache):
    db.add_emoji("acc_a", "https://cdn/a", md5=PNG_MD5)
    blob = _zip_bytes(
        {"x.png": PNG_BYTES},
        [{"file": "x.png", "tags": [], "description": "补上的描述"}],
    )
    resp = api.post(
        "/api/emojis/import",
        files=[("files", ("desc.zip", blob, "application/zip"))],
    )
    assert resp.status_code == 200
    assert resp.json()["updated"] == 1
    assert db.list_emojis("acc_a")[0]["description"] == "补上的描述"


def test_import_rejects_oversized_request(api, emoji_cache, monkeypatch):
    monkeypatch.setattr(main, "IMPORT_REQUEST_MAX_BYTES", 10)
    resp = api.post(
        "/api/emojis/import",
        files=[("files", ("a.png", PNG_BYTES, "image/png"))],
    )
    assert resp.status_code == 413


def test_export_stream_cleans_temp_file_on_abort(tmp_path):
    """客户端中断时生成器被关闭，临时 zip 也必须删掉。"""
    import asyncio

    path = tmp_path / "aborted.zip"
    path.write_bytes(b"x" * 1024)

    async def run():
        stream = main._stream_export_file(path)
        assert await stream.__anext__() == b"x" * 1024
        await stream.aclose()

    asyncio.run(run())
    assert not path.exists()


# ------------------------------------------------------------------ 表情锁定
# 锁定只冻结「改标签/描述、AI 重打标、删除、导入合并」；发送、导出、查看照常。

def test_lock_endpoint_and_put_conflict(api):
    row = db.add_emoji("acc_a", "https://cdn/a", md5=MD5_A)
    locked = api.post(f"/api/emojis/{row['id']}/lock", json={"locked": True})
    assert locked.status_code == 200
    assert locked.json()["emoji"]["locked"] == 1
    assert api.put(
        f"/api/emojis/{row['id']}", json={"tags": "新", "description": "新"}
    ).status_code == 409
    unlocked = api.post(f"/api/emojis/{row['id']}/lock", json={"locked": False})
    assert unlocked.json()["emoji"]["locked"] == 0
    assert api.put(
        f"/api/emojis/{row['id']}", json={"tags": "新", "description": "新"}
    ).status_code == 200


def test_lock_endpoint_404_for_missing_or_foreign(api):
    assert api.post("/api/emojis/999/lock", json={"locked": True}).status_code == 404
    foreign = db.add_emoji("acc_b", "https://cdn/b", md5="b" * 32)
    assert api.post(
        f"/api/emojis/{foreign['id']}/lock", json={"locked": True}
    ).status_code == 404


def test_retag_and_delete_locked_409(api, emoji_cache, monkeypatch):
    row = db.add_emoji("acc_a", "https://cdn/a", md5=MD5_A)
    _seed_cache(row["url"], MD5_A)
    db.set_emoji_locked("acc_a", row["id"], True)
    monkeypatch.setattr(
        main, "session_of",
        lambda request: FakeSession(FakeLLM('{"tags": ["无语"]}')),
    )
    assert api.post(f"/api/emojis/{row['id']}/retag").status_code == 409
    assert api.delete(f"/api/emojis/{row['id']}").status_code == 409
    db.set_emoji_locked("acc_a", row["id"], False)
    assert api.delete(f"/api/emojis/{row['id']}").status_code == 200


def test_batch_delete_skips_locked(api):
    a = db.add_emoji("acc_a", "https://cdn/a", md5=MD5_A)
    b = db.add_emoji("acc_a", "https://cdn/b", md5="b" * 32)
    db.set_emoji_locked("acc_a", b["id"], True)
    resp = api.post("/api/emojis/batch-delete", json={"ids": [a["id"], b["id"]]})
    assert resp.status_code == 200
    assert resp.json()["deleted"] == 1
    assert resp.json()["skipped_locked"] == 1
    assert db.get_emoji("acc_a", b["id"]) is not None


def test_locked_409_detail_messages_match_contract(api, emoji_cache, monkeypatch):
    """409 文案是给控制台直接展示的合同，不能随意改。"""
    row = db.add_emoji("acc_a", "https://cdn/a", md5=MD5_A)
    _seed_cache(row["url"], MD5_A)
    db.set_emoji_locked("acc_a", row["id"], True)
    monkeypatch.setattr(
        main, "session_of", lambda request: FakeSession(FakeLLM('{"tags": ["无语"]}'))
    )
    assert api.put(f"/api/emojis/{row['id']}", json={"tags": "x"}).json()["detail"] == "已锁定，先解锁再修改"
    assert api.post(f"/api/emojis/{row['id']}/retag").json()["detail"] == "已锁定，先解锁再重新打标"
    assert api.delete(f"/api/emojis/{row['id']}").json()["detail"] == "已锁定，先解锁再删除"
    # 锁定行既没被删也没被打标，缓存也还在
    fresh = db.get_emoji("acc_a", row["id"])
    assert fresh["tags"] == "" and fresh["locked"] == 1
    assert emoji_cache / f"{MD5_A}.gif" in list(emoji_cache.glob("*"))


def test_retag_locked_409_before_touching_llm_or_cache(api, emoji_cache, monkeypatch):
    """锁校验必须排在模型调用与缓存检查之前：锁定行永远进不了 reject_emoji。"""
    calls: list[str] = []
    row = db.add_emoji("acc_a", "https://cdn/a", md5=MD5_A)
    db.set_emoji_locked("acc_a", row["id"], True)

    class ExplodingLLM:
        configured = True

        async def stream(self, messages, **kwargs):
            calls.append("llm")
            yield {"type": "text", "text": '{"not_sticker": true}'}

    monkeypatch.setattr(main, "session_of", lambda request: FakeSession(ExplodingLLM()))
    assert api.post(f"/api/emojis/{row['id']}/retag").status_code == 409
    assert calls == []
    assert db.get_emoji("acc_a", row["id"]) is not None


def test_retag_not_sticker_keeps_cache_when_reject_blocked(api, emoji_cache, monkeypatch):
    """兜底：reject_emoji 对锁定行返回 None 时，不能把共享缓存文件删掉。"""
    from app.emoji import cache_path

    row = db.add_emoji("acc_a", "https://cdn/a", md5=MD5_A)
    _seed_cache(row["url"], MD5_A)
    monkeypatch.setattr(
        main, "session_of", lambda request: FakeSession(FakeLLM('{"not_sticker": true}'))
    )
    # 模拟并发中被锁定：数据层拒绝删除，接口要报错而不是清缓存
    monkeypatch.setattr(db, "reject_emoji", lambda *args, **kwargs: None)
    resp = api.post(f"/api/emojis/{row['id']}/retag")
    assert resp.status_code == 409
    assert resp.json()["detail"] == "已锁定，先解锁再删除"
    assert cache_path(row["url"], MD5_A).is_file()
    assert db.get_emoji("acc_a", row["id"]) is not None
    assert db.list_reject_md5s("acc_a") == set()


def test_import_does_not_merge_into_locked_row(api, emoji_cache):
    row, status = db.import_site_emoji("acc_a", PNG_MD5, ["串子站"], "旧描述")
    assert status == "added" and row["locked"] == 1
    blob = _zip_bytes(
        {"x.png": PNG_BYTES},
        [{"file": "x.png", "md5": PNG_MD5, "tags": ["新标签"], "description": "新描述"}],
    )
    resp = api.post(
        "/api/emojis/import", files=[("files", ("locked.zip", blob, "application/zip"))]
    )
    assert resp.status_code == 200
    body = resp.json()
    assert (body["added"], body["updated"]) == (0, 0)
    fresh = db.get_emoji_by_md5("acc_a", PNG_MD5)
    assert fresh["tags"] == "串子站"
    assert fresh["description"] == "旧描述"
    assert fresh["locked"] == 1


def test_locked_emoji_can_still_be_sent(api, monkeypatch):
    """锁定不拦发送：表情墙「发送」按钮走的 /api/chats/{uid}/send-emoji 照旧成功。"""
    from app import config

    row = db.add_emoji("acc_a", "https://cdn/a", md5=MD5_A, width=200, height=200)
    db.set_emoji_locked("acc_a", row["id"], True)
    calls: list[tuple] = []

    class SendSession:
        llm = None

        async def send_emoji(self, uid, info, source="agent"):
            calls.append((uid, info, source))
            return {"id": 7, "content": "[动画表情]"}

    monkeypatch.setattr(main, "session_of", lambda request: SendSession())
    # 隔离开发机真实配置：既不让访问令牌拦路，也不带入接管暂停的副作用
    monkeypatch.setattr(config, "load", lambda: {"auto": {}, "server": {}})

    resp = api.post(
        "/api/chats/u1/send-emoji",
        json={
            "url": row["url"],
            "md5": row["md5"],
            "width": row["width"],
            "height": row["height"],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert len(calls) == 1
    assert calls[0][0] == "u1"
    assert calls[0][1]["md5"] == MD5_A
    assert calls[0][2] == "human"
    # 发送既不解锁也不改行
    assert db.get_emoji("acc_a", row["id"])["locked"] == 1


def test_locked_emoji_can_still_be_exported(api, emoji_cache):
    """锁定不拦导出：图片与 manifest 照常打包，标签/描述/锁原样保留。"""
    row = db.add_emoji("acc_a", "https://cdn/a", md5=MD5_A)
    db.set_emoji_tags("acc_a", row["id"], "串子站,无语", "锁定的串子图")
    _seed_cache(row["url"], MD5_A)
    db.set_emoji_locked("acc_a", row["id"], True)

    resp = api.post("/api/emojis/batch-zip", json={"ids": [row["id"]]})
    assert resp.status_code == 200
    assert resp.headers["x-emoji-packed"] == "1"
    assert resp.headers["x-emoji-skipped"] == "0"
    names = zipfile.ZipFile(io.BytesIO(resp.content)).namelist()
    assert f"{row['id']}_{MD5_A[:24]}.gif" in names
    item = _manifest_of(resp.content)["items"][0]
    assert item["md5"] == MD5_A
    assert item["tags"] == ["串子站", "无语"]
    assert item["description"] == "锁定的串子图"

    fresh = db.get_emoji("acc_a", row["id"])
    assert fresh["locked"] == 1
    assert fresh["tags"] == "串子站,无语"

    # 「导出全部」同样把锁定表情算进去
    resp_all = api.post("/api/emojis/batch-zip", json={"all": True})
    assert resp_all.status_code == 200
    assert len(_manifest_of(resp_all.content)["items"]) == 1


def test_locked_emoji_can_still_be_proxied(api, emoji_cache):
    """锁定不拦查看/下载：代取端点只认 md5 缓存，200 且内容完整。"""
    row = db.add_emoji("acc_a", "https://cdn/a", md5=MD5_A)
    _seed_cache(row["url"], MD5_A)
    db.set_emoji_locked("acc_a", row["id"], True)

    for extra in ({}, {"download": 1}):
        resp = api.get("/api/emoji/proxy", params={"url": row["url"], "md5": MD5_A, **extra})
        assert resp.status_code == 200
        assert resp.content == PNG_BYTES
    assert db.get_emoji("acc_a", row["id"])["locked"] == 1


# ------------------------------------------------------------------ 标签锁

def test_tag_lock_api_and_consolidate_skips(api, monkeypatch):
    from app.ai import tagger

    row = db.add_emoji("acc_a", "https://cdn/a", md5=MD5_A)
    db.set_emoji_tags("acc_a", row["id"], "串子站,无语", "")
    other = db.add_emoji("acc_a", "https://cdn/b", md5="b" * 32)
    db.set_emoji_tags("acc_a", other["id"], "串子站,加班", "")

    locked = api.post(
        "/api/emoji/tag-locks", json={"tag": "串子站", "locked": True}
    )
    assert locked.status_code == 200
    assert locked.json()["tags"] == ["串子站"]
    assert api.get("/api/emoji/tag-locks").json()["tags"] == ["串子站"]

    async def fake_consolidate(llm, freq):
        return {"串子站": "表情", "无语": "无语子"}

    monkeypatch.setattr(tagger, "consolidate_tags", fake_consolidate)
    monkeypatch.setattr(
        main, "session_of",
        lambda request: FakeSession(FakeLLM("{}")),
    )
    resp = api.post("/api/emojis/consolidate-tags")
    assert resp.status_code == 200
    body = resp.json()
    assert "串子站" not in body["mapping"]
    assert body["locked_tags_skipped"] == 1
    assert db.get_emoji("acc_a", row["id"])["tags"] == "串子站,无语子"
    assert db.get_emoji("acc_a", other["id"])["tags"] == "串子站,加班"


def test_tag_lock_requires_existing_tag(api):
    resp = api.post(
        "/api/emoji/tag-locks", json={"tag": "不存在", "locked": True}
    )
    assert resp.status_code == 400


def test_tag_lock_rejects_empty_or_multiple_tags(api):
    assert api.post("/api/emoji/tag-locks", json={"tag": "", "locked": True}).status_code == 400
    assert api.post(
        "/api/emoji/tag-locks", json={"tag": "无语，加班", "locked": True}
    ).status_code == 400


def test_tag_lock_unlock_allowed_after_tag_vanishes(api):
    """解锁永远放行：带该标签的表情删光后，锁记录还得能取消。"""
    row = db.add_emoji("acc_a", "https://cdn/a", md5=MD5_A)
    db.set_emoji_tags("acc_a", row["id"], "串子站", "")
    assert api.post("/api/emoji/tag-locks", json={"tag": "串子站", "locked": True}).status_code == 200
    assert db.remove_emoji("acc_a", row["id"]) is True
    assert db.tag_frequency("acc_a") == []
    resp = api.post("/api/emoji/tag-locks", json={"tag": "串子站", "locked": False})
    assert resp.status_code == 200
    assert resp.json()["tags"] == []
    # 标签已经不存在，再锁就报 400
    assert api.post("/api/emoji/tag-locks", json={"tag": "串子站", "locked": True}).status_code == 400


def test_tag_lock_route_not_shadowed_by_emoji_id_routes(api):
    """路由是单数 /api/emoji/tag-locks，不能被 /api/emojis/{eid} 系列抢走。"""
    assert api.get("/api/emoji/tag-locks").status_code == 200
    assert api.get("/api/emoji/proxy").status_code == 422  # 原有代取路由缺 url 参数
    assert api.get("/api/emojis/1").status_code == 405  # 该 id 路径没有 GET 路由


def test_settings_default_chuanzi_off():
    from app import config
    assert config.DEFAULTS["ai"]["chuanzi_mode"] is False
    # 串子站表情包是独立开关，默认关闭；已入库的站内图不受影响
    assert config.DEFAULTS["ai"]["chuanzi_site_emoji"] is False


# ----------------------------------------------------- 数据层回归（v3→v4 迁移）

def test_legacy_v3_db_migration_adds_lock_columns(tmp_path, monkeypatch):
    """老库（v3，emojis 无 source/locked）升级后补列、建标签锁表、版本号落 4。"""
    import sqlite3

    path = tmp_path / "legacy.db"
    seed = sqlite3.connect(path)
    seed.executescript(
        """
        CREATE TABLE emojis (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id TEXT NOT NULL,
            url TEXT NOT NULL,
            tags TEXT DEFAULT '',
            description TEXT DEFAULT '',
            created_at REAL,
            UNIQUE(account_id, url)
        );
        INSERT INTO emojis(account_id, url, tags, description, created_at)
            VALUES('acc_a', 'https://cdn/a', '无语,加班', '熊猫头', 1.0);
        """
    )
    seed.execute("PRAGMA user_version=3")
    seed.commit()
    seed.close()

    monkeypatch.setattr(db, "DB_PATH", path)
    old = getattr(db._local, "conn", None)
    if old is not None:
        old.close()
        delattr(db._local, "conn")
    try:
        db.init()
        conn = db._conn()
        cols = db._columns(conn, "emojis")
        assert {"source", "locked"} <= cols
        row = db.get_emoji("acc_a", 1)
        assert row is not None
        assert row["source"] == ""
        assert row["locked"] == 0
        # 迁移不能吃掉存量标注
        assert row["tags"] == "无语,加班"
        assert row["description"] == "熊猫头"
        assert db._has_table(conn, "emoji_tag_locks")
        assert db.list_locked_tags("acc_a") == []
        # _migrate 会先把非空库的 user_version 复位到 2 再逐级补 stamp，只看最终值
        # v5 = 记忆分层（layer/subject/embedding…）
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 6
        assert {"layer", "subject", "embedding"} <= db._columns(conn, "memories")
    finally:
        conn = getattr(db._local, "conn", None)
        if conn is not None:
            conn.close()
            delattr(db._local, "conn")


def test_reject_emoji_locked_row_kept_and_unlisted(temp_db):
    row = db.add_emoji("acc_a", "https://cdn/a", md5=MD5_A)
    db.set_emoji_locked("acc_a", row["id"], True)
    assert db.reject_emoji("acc_a", row["id"], "误判") is None
    assert db.get_emoji("acc_a", row["id"]) is not None
    assert db.list_reject_md5s("acc_a") == set()

    db.set_emoji_locked("acc_a", row["id"], False)
    assert db.reject_emoji("acc_a", row["id"], "非表情包") == row
    assert db.get_emoji("acc_a", row["id"]) is None
    assert db.list_reject_md5s("acc_a") == {MD5_A}

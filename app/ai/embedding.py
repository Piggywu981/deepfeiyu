"""可选 embedding 客户端：任何 OpenAI 兼容的 /embeddings 接口。

记忆检索默认走关键词，不配置这里也能用；配置后按向量余弦做语义召回。
密钥只从 ``memory.embedding.key_env`` 指向的环境变量读取，不落 config.json。
"""
from __future__ import annotations

import os
from typing import Any, Callable

import httpx

from .. import config


class EmbeddingError(RuntimeError):
    pass


class EmbeddingClient:
    def __init__(self, get_cfg: Callable[[], dict[str, Any]] | None = None) -> None:
        self._get_cfg = get_cfg or config.load

    @property
    def cfg(self) -> dict[str, Any]:
        return ((self._get_cfg().get("memory") or {}).get("embedding") or {})

    @property
    def configured(self) -> bool:
        cfg = self.cfg
        return bool(
            cfg.get("enabled")
            and str(cfg.get("base_url") or "").strip()
            and str(cfg.get("model") or "").strip()
            and self._key()
        )

    def _key(self) -> str:
        env_name = str(self.cfg.get("key_env") or "EMBEDDING_API_KEY").strip()
        return (os.environ.get(env_name) or "").strip() if env_name else ""

    def _url(self) -> str:
        base = str(self.cfg.get("base_url") or "").strip().rstrip("/")
        if not base:
            return ""
        if base.endswith("/embeddings"):
            return base
        return base + "/embeddings"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if not self.configured:
            raise EmbeddingError("embedding 未配置")
        cfg = self.cfg
        model = str(cfg.get("model") or "").strip()
        timeout = float(cfg.get("timeout") or 20)
        out: list[list[float]] = []
        # 大多数兼容接口单次 32 条足够；分批避免请求体过大
        for start in range(0, len(texts), 32):
            batch = [str(t or "").strip()[:6000] for t in texts[start : start + 32]]
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.post(
                        self._url(),
                        headers={
                            "Authorization": f"Bearer {self._key()}",
                            "Content-Type": "application/json",
                        },
                        json={"model": model, "input": batch},
                    )
            except Exception as exc:  # noqa: BLE001
                raise EmbeddingError(f"embedding 请求失败：{exc}") from exc
            if resp.status_code >= 400:
                detail = resp.text[:200].replace("\n", " ")
                raise EmbeddingError(f"embedding 接口 {resp.status_code}：{detail}")
            try:
                data = resp.json()
                items = sorted(data.get("data") or [], key=lambda x: int(x.get("index", 0)))
                vectors = [list(map(float, item["embedding"])) for item in items]
            except Exception as exc:  # noqa: BLE001
                raise EmbeddingError(f"embedding 返回格式不对：{exc}") from exc
            if len(vectors) != len(batch):
                raise EmbeddingError(
                    f"embedding 返回条数不对：要 {len(batch)} 条，实际 {len(vectors)} 条"
                )
            out.extend(vectors)
        return out

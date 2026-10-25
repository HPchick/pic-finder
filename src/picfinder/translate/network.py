"""OpenAI 兼容接口的翻译来源。

对应 DD-04 与 DD-05：接口是可选补充，密钥由用户自填，未配置时软件保持完整可用。
因此本模块的任何失败都只能降级为提示，不得影响本地词典的结果。

接口地址与模型名由用户填写，不内置任何服务商。成本由用户承担，因此界面需要
显示本次消耗的大致令牌数，对应 DD-05 的影响一项。
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .. import logging_setup
from ..config import Translation as TranslationConfig
from .types import SOURCE_NETWORK, TagCandidate

logger = logging_setup.get("translate.network")

DEFAULT_TIMEOUT = 30
CACHE_DAYS = 30

SYSTEM_PROMPT = """你是图像检索标签的翻译助手。用户给出中文描述，你输出对应的英文图像标签。

规则：
1. 只输出标签，标签之间用英文逗号分隔，不要输出解释、编号或标点以外的内容。
2. 标签必须是小写英文，多个单词之间用下划线连接，例如 looking_at_viewer。
3. 优先使用 Danbooru 图库的标签写法。
4. 最多输出 8 个标签，按重要性从高到低排列。
5. 如果某个中文意象没有对应的常见标签，跳过它，不要编造。"""

USER_TEMPLATE = """参考图类别：{category_label}
中文描述：{text}

只输出逗号分隔的标签。"""

CATEGORY_LABELS = {
    "pose": "人体姿势",
    "outfit": "服装",
    "prop": "道具",
    "scene": "场景建筑",
}

# 允许出现的标签字符。任何不符合的片段都说明模型输出了额外内容。
TAG_PATTERN = re.compile(r"^[a-z0-9_()\-'\.]+$")


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class NetworkOutcome:
    """一次接口调用的结果。"""

    candidates: list[TagCandidate]
    used_cache: bool = False
    token_usage: int | None = None
    notice: str = ""


def prompt_fingerprint() -> str:
    """提示词的指纹。提示词改动之后缓存自动失效。"""
    payload = SYSTEM_PROMPT + "\n---\n" + USER_TEMPLATE
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _input_hash(text: str, category_slug: str) -> str:
    return hashlib.sha256(f"{category_slug}\n{text}".encode("utf-8")).hexdigest()[:32]


def parse_tags(content: str) -> list[str]:
    """从模型输出里提取标签。

    模型经常不听话，会加上编号、引号、句号或解释文字。这里做一次清洗，
    只保留看起来像标签的片段。
    """
    if not content:
        return []

    cleaned = content.strip()
    # 去掉常见的包裹形式。
    cleaned = cleaned.strip("`").strip()
    cleaned = re.sub(r"^```[a-z]*\n?", "", cleaned)
    cleaned = cleaned.replace("\n", ",")

    tags: list[str] = []
    for piece in cleaned.split(","):
        token = piece.strip().strip("\"'`。.、：:；;").strip()
        token = token.replace(" ", "_").lower()
        if not token:
            continue
        # 丢掉编号前缀，例如 1. standing。
        token = re.sub(r"^\d+[\.\)]\s*", "", token)
        if not token or not TAG_PATTERN.match(token):
            continue
        if token in tags:
            continue
        tags.append(token)
        if len(tags) >= 12:
            break
    return tags


class NetworkProvider:
    """接口翻译来源。未配置或调用失败时返回空结果并给出提示。"""

    def __init__(self, connection: sqlite3.Connection, config: TranslationConfig) -> None:
        self.connection = connection
        self.config = config

    @property
    def configured(self) -> bool:
        return bool(
            self.config.enabled
            and self.config.base_url.strip()
            and self.config.model.strip()
        )

    def cache_lookup(self, text: str, category_slug: str) -> list[TagCandidate] | None:
        if not self.configured:
            return None
        key = _input_hash(text, category_slug)
        row = self.connection.execute(
            """SELECT output_json, expires_at FROM translation_cache
               WHERE input_hash = ? AND provider = ? AND model = ? AND prompt_hash = ?""",
            (key, self.config.base_url, self.config.model, prompt_fingerprint()),
        ).fetchone()
        if row is None:
            return None
        if row["expires_at"] and row["expires_at"] < utc_now():
            return None
        try:
            payload = json.loads(row["output_json"])
        except (json.JSONDecodeError, TypeError):
            return None
        if not isinstance(payload, list):
            return None
        return [
            TagCandidate(tag=str(item), weight=0.6, source=SOURCE_NETWORK, origin=text)
            for item in payload
            if item
        ]

    def cache_store(self, text: str, category_slug: str, tags: list[str],
                    token_usage: int | None) -> None:
        key = _input_hash(text, category_slug)
        expires = (
            datetime.now(timezone.utc) + timedelta(days=CACHE_DAYS)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.connection.execute(
            """INSERT INTO translation_cache
               (input_text, input_hash, provider, model, prompt_hash,
                output_json, token_usage, created_at, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (input_hash, provider, model, prompt_hash)
               DO UPDATE SET output_json = excluded.output_json,
                             token_usage = excluded.token_usage,
                             created_at = excluded.created_at,
                             expires_at = excluded.expires_at""",
            (
                text,
                key,
                self.config.base_url,
                self.config.model,
                prompt_fingerprint(),
                json.dumps(tags, ensure_ascii=False),
                token_usage,
                utc_now(),
                expires,
            ),
        )
        self.connection.commit()

    def translate(self, text: str, category_slug: str) -> NetworkOutcome:
        """调用接口翻译。任何失败都返回提示而不是抛出。"""
        if not self.configured:
            return NetworkOutcome(candidates=[], notice="")

        cached = self.cache_lookup(text, category_slug)
        if cached is not None:
            return NetworkOutcome(candidates=cached, used_cache=True)

        try:
            import httpx
        except ImportError:
            return NetworkOutcome(candidates=[], notice="未安装 httpx，接口翻译不可用。")

        payload = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": USER_TEMPLATE.format(
                        category_label=CATEGORY_LABELS.get(category_slug, category_slug),
                        text=text,
                    ),
                },
            ],
            "temperature": 0.2,
        }

        headers = {"Content-Type": "application/json"}
        if self.config.api_key.strip():
            headers["Authorization"] = f"Bearer {self.config.api_key.strip()}"

        url = self.config.base_url.strip().rstrip("/") + "/chat/completions"

        try:
            with httpx.Client(timeout=float(self.config.timeout_seconds or DEFAULT_TIMEOUT)) as client:
                response = client.post(url, json=payload, headers=headers)
        except Exception as exc:  # noqa: BLE001
            logger.warning("接口请求失败：%s", exc)
            return NetworkOutcome(candidates=[], notice=f"接口请求失败：{exc}")

        if response.status_code != 200:
            detail = response.text[:200].replace("\n", " ")
            logger.warning("接口返回 %s：%s", response.status_code, detail)
            return NetworkOutcome(
                candidates=[],
                notice=f"接口返回 {response.status_code}，已忽略接口结果。",
            )

        try:
            body = response.json()
            content = body["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            logger.warning("接口返回结构无法解析：%s", exc)
            return NetworkOutcome(candidates=[], notice="接口返回结构无法解析，已忽略。")

        tags = parse_tags(content)
        usage = None
        usage_block = body.get("usage") if isinstance(body, dict) else None
        if isinstance(usage_block, dict):
            total = usage_block.get("total_tokens")
            if isinstance(total, int):
                usage = total

        if tags:
            self.cache_store(text, category_slug, tags, usage)

        return NetworkOutcome(
            candidates=[
                TagCandidate(tag=tag, weight=0.6, source=SOURCE_NETWORK, origin=text)
                for tag in tags
            ],
            token_usage=usage,
            notice="" if tags else "接口没有返回可用标签。",
        )

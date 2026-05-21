"""AI 分析层：给图片打标签、判断当前语境是否适合发表情包。"""

import base64
import json
import os
from collections import defaultdict, deque

from astrbot.api import logger


class MemeAnalyzer:
    # 最多保留每个群的近 N 条文本记录
    _HISTORY_MAXLEN = 15

    def __init__(self, context):
        self._ctx = context  # astrbot Context
        self._history: dict[str, deque] = defaultdict(
            lambda: deque(maxlen=self._HISTORY_MAXLEN)
        )

    # ------------------------------------------------------------------
    # 历史消息管理
    # ------------------------------------------------------------------
    def push(self, group_id: str, text: str):
        if text:
            self._history[group_id].append(text)

    def get_history(self, group_id: str) -> list[str]:
        return list(self._history[group_id])

    # ------------------------------------------------------------------
    # LLM 调用工具（后台专用，不绑定群组会话）
    # ------------------------------------------------------------------
    async def _llm(
        self,
        prompt: str,
        *,
        image_b64: str | None = None,
    ) -> str:
        """调用全局 LLM，不传 umo，避免将后台请求写入群组对话历史。"""
        # 始终使用全局默认 provider（umo=None），防止污染群聊会话记录
        provider = self._ctx.get_using_provider(None)
        if provider is None:
            providers = self._ctx.get_all_providers()
            if not providers:
                return ""
            provider = providers[0]

        image_urls: list[str] = []
        if image_b64:
            image_urls = [f"data:image/jpeg;base64,{image_b64}"]

        try:
            resp = await provider.text_chat(
                prompt=prompt,
                image_urls=image_urls,
                contexts=[],   # 空上下文，不携带任何会话历史
            )
            return resp.completion_text or ""
        except Exception as e:
            logger.warning(f"[Meme] LLM 调用失败: {e}")
            return ""

    # ------------------------------------------------------------------
    # 图片打标签
    # ------------------------------------------------------------------
    async def tag_image(self, image_path: str, umo: str | None = None) -> list[str]:
        """用视觉模型分析表情包，返回中文标签列表（最多 6 个）。
        umo 参数保留但不再传给 _llm，只供日志追踪用。
        """
        try:
            with open(image_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
        except Exception as e:
            logger.warning(f"[Meme] 读取图片失败（打标签）: {e}")
            return []

        prompt = (
            "请分析这张表情包图片，给出 3-6 个简短中文情绪/内容标签。"
            "常见标签示例：搞笑、无语、开心、悲伤、愤怒、惊讶、可爱、"
            "比心、加油、委屈、得意、吃瓜、打工、摸鱼、蚌埠住了。"
            "只返回 JSON 数组，例如：[\"搞笑\", \"无语\", \"狗头\"]，不要其他内容。"
        )

        raw = await self._llm(prompt, image_b64=b64)
        return _parse_json_list(raw)

    # ------------------------------------------------------------------
    # 语境判断：是否应该发表情包
    # ------------------------------------------------------------------
    async def should_send_meme(
        self, group_id: str, umo: str | None = None
    ) -> tuple[bool, list[str]]:
        """根据近期聊天记录判断是否适合插入表情包。
        umo 参数保留但不再传给 _llm，只供日志追踪用。

        Returns:
            (should_send, mood_tags)
        """
        history = self.get_history(group_id)
        if len(history) < 2:
            return False, []

        recent = "\n".join(history[-8:])
        prompt = (
            "以下是群聊近期消息（最新在最后）：\n"
            f"{recent}\n\n"
            "请判断现在是否是发一个表情包的好时机（例如：话题变得搞笑、"
            "出现情绪高点、适合用表情包表达共鸣等）。\n"
            "若适合，返回 JSON：{\"send\": true, \"tags\": [\"标签1\", \"标签2\"]}\n"
            "若不适合，返回：{\"send\": false, \"tags\": []}\n"
            "只返回 JSON，不要任何其他内容。"
        )

        raw = await self._llm(prompt)
        data = _parse_json_obj(raw)
        if data:
            return bool(data.get("send", False)), list(data.get("tags", []))
        return False, []

    # ------------------------------------------------------------------
    # 生成 AI 表情包文案
    # ------------------------------------------------------------------
    async def generate_meme_text(
        self, group_id: str, umo: str | None = None
    ) -> dict | None:
        """根据群聊语境生成表情包文案。
        umo 参数保留但不再传给 _llm，只供日志追踪用。

        Returns:
            {"top": str, "bottom": str, "style": str} 或 None
        """
        history = self.get_history(group_id)
        if not history:
            return None

        recent = "\n".join(history[-6:])
        prompt = (
            "以下是群聊近期内容：\n"
            f"{recent}\n\n"
            "请根据这个语境，生成一个有趣的中文梗图文案。\n"
            "格式要求：上方一句话（不超过 14 字），下方一句话（不超过 14 字），"
            "再给出一个情绪风格关键词（如：搞笑/无语/开心/悲伤/惊讶/可爱/加油）。\n"
            "只返回 JSON：{\"top\": \"上方文字\", \"bottom\": \"下方文字\", \"style\": \"风格\"}，"
            "不要其他内容。"
        )

        raw = await self._llm(prompt)
        return _parse_json_obj(raw)


# ------------------------------------------------------------------
# 辅助：解析 LLM 返回的 JSON
# ------------------------------------------------------------------
def _parse_json_list(raw: str) -> list:
    if not raw:
        return []
    try:
        s = raw.find("[")
        e = raw.rfind("]") + 1
        if s != -1 and e > s:
            return json.loads(raw[s:e])
    except Exception:
        pass
    return []


def _parse_json_obj(raw: str) -> dict | None:
    if not raw:
        return None
    try:
        s = raw.find("{")
        e = raw.rfind("}") + 1
        if s != -1 and e > s:
            return json.loads(raw[s:e])
    except Exception:
        pass
    return None

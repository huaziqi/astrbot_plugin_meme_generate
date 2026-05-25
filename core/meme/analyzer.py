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
    # Provider ID 配置（与 AstrBot 网页设置中的 Provider ID 对应）
    # ------------------------------------------------------------------
    # 商源 ID（AstrBot 网页 → 模型提供商 → 商源 → ID 字段，即 provider_sources[].id）
    # 注意：这里填的是"商源唯一 ID"，不是模型实例 ID
    _TEXT_SOURCE_ID  = "text"   # 纯文字任务（语境判断、文案生成）
    _IMAGE_SOURCE_ID = "image"  # 视觉任务（表情包打标签）
    _DRAW_SOURCE_ID  = "draw"   # AI 生图任务（images.generate 接口）

    # ------------------------------------------------------------------
    # 按商源 ID 查找 provider
    # ------------------------------------------------------------------
    def _get_provider_by_source(self, source_id: str):
        """在所有已加载的 provider 中，找 provider_source_id == source_id 的那个。

        这样无需关心模型实例的自动生成 ID（如"图像/wan2.7-image-pro"），
        只要商源 ID 对得上就能找到。
        """
        for p in self._ctx.get_all_providers():
            if p.provider_config.get("provider_source_id") == source_id:
                return p
        # 兼容旧式配置：直接用 source_id 作为实例 id 的情况
        return self._ctx.get_provider_by_id(source_id)

    # ------------------------------------------------------------------
    # LLM 调用工具（后台专用，不绑定群组会话）
    # ------------------------------------------------------------------
    def _get_chat_provider(self, need_vision: bool = False):
        """按任务类型获取 Chat Completion provider。

        Args:
            need_vision: True → 视觉模型（image 商源），False → 文字模型（text 商源）。
        """
        source_id = self._IMAGE_SOURCE_ID if need_vision else self._TEXT_SOURCE_ID
        provider = self._get_provider_by_source(source_id)
        if provider is not None:
            return provider

        logger.warning(
            f"[Meme] 未找到商源 '{source_id}' 下的 provider，降级使用默认 provider。"
        )
        fallback = self._ctx.get_using_provider(None)
        if fallback is not None:
            return fallback
        providers = self._ctx.get_all_providers()
        return providers[0] if providers else None

    async def _llm(
        self,
        prompt: str,
        *,
        image_b64: str | None = None,
    ) -> str:
        """调用 Chat LLM，不传 umo，避免将后台请求写入群组对话历史。

        有图片时自动选用 image provider，纯文字时选用 text provider。
        """
        need_vision = image_b64 is not None
        provider = self._get_chat_provider(need_vision=need_vision)
        if provider is None:
            logger.warning("[Meme] 没有可用的 LLM provider")
            return ""

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
    # AI 生图（调用独立的 draw provider）
    # ------------------------------------------------------------------
    async def draw_image(self, prompt: str, output_dir: str) -> "str | None":
        """调用 draw provider 的图像生成接口，返回本地文件路径；失败返回 None。

        draw provider 需在 AstrBot 网页中配置为 OpenAI 兼容接口
        （如 DALL-E 3、Stability AI、SiliconFlow 等），Provider ID 填 "draw"。

        支持两种返回格式：
          - b64_json：优先尝试，SiliconFlow / 大部分国内中转站使用此格式
          - url：DALL-E 3 官方及部分服务使用此格式，需要额外下载
        """
        import base64
        import uuid

        import httpx
        from openai import AsyncOpenAI

        provider = self._get_provider_by_source(self._DRAW_SOURCE_ID)
        if provider is None:
            logger.warning(
                f"[Meme] 未找到商源 '{self._DRAW_SOURCE_ID}' 下的 provider，"
                "跳过 AI 生图，将降级为本地 PIL 渲染。"
                "请在 AstrBot 网页 → 模型提供商 中新增一个商源，将其商源唯一 ID 设为 'draw'，"
                "并在该商源下添加一个支持文生图的模型（如 wan2.7-image-pro）。"
            )
            return None

        cfg = provider.provider_config
        # 优先从 provider 对象读取已解析的模型名，再兜底读 config["model"]
        model    = provider.get_model() or cfg.get("model", "dall-e-3")
        api_key  = cfg.get("key", [""])[0] if cfg.get("key") else ""
        api_base = cfg.get("api_base") or "https://api.openai.com/v1"

        logger.info(f"[Meme] 调用 draw provider: model={model}, base={api_base}")
        client = AsyncOpenAI(api_key=api_key, base_url=api_base)

        image_bytes: bytes | None = None

        # ── 优先尝试 b64_json（国内大多数 API 兼容此格式）────────────
        try:
            resp = await client.images.generate(
                model=model,
                prompt=prompt,
                n=1,
                size="1024x1024",
                response_format="b64_json",
            )
            raw_b64 = resp.data[0].b64_json
            if raw_b64:
                image_bytes = base64.b64decode(raw_b64)
                logger.info("[Meme] 生图成功（b64_json 格式）")
        except Exception as e:
            logger.warning(f"[Meme] b64_json 格式失败，尝试 url 格式: {e}")

        # ── 备选：url 格式（DALL-E 3 官方等）────────────────────────
        if image_bytes is None:
            try:
                resp = await client.images.generate(
                    model=model,
                    prompt=prompt,
                    n=1,
                    size="1024x1024",
                    response_format="url",
                )
                image_url = resp.data[0].url
                if image_url:
                    async with httpx.AsyncClient(timeout=30) as http:
                        r = await http.get(image_url)
                        r.raise_for_status()
                    image_bytes = r.content
                    logger.info("[Meme] 生图成功（url 格式）")
            except Exception as e:
                logger.warning(f"[Meme] url 格式也失败，放弃 AI 生图: {e}")
                return None

        if not image_bytes:
            return None

        # ── 保存到本地 ────────────────────────────────────────────────
        os.makedirs(output_dir, exist_ok=True)
        out_path = os.path.join(output_dir, f"ai_draw_{uuid.uuid4().hex[:10]}.png")
        with open(out_path, "wb") as f:
            f.write(image_bytes)
        logger.info(f"[Meme] AI 生图保存完成: {out_path}")
        return out_path

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

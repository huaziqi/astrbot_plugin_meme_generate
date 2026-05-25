"""AstrBot 插件：TTS 语音合成 + 表情包自动收集 & AI 生成发送。

表情包功能说明
──────────────
1. 自动收集：群聊中发送的图片会被保存，并用视觉大模型打标签存入 SQLite。
2. 自动发送：根据群聊上下文，AI 判断是否适合插入一张表情包。
3. AI 生成：无库存匹配时，调用 draw provider 生图 / PIL 本地降级渲染。
4. 显式指令：/meme [描述] 立即生成；群聊中说"发个表情包"等关键词也会触发。
"""

import hashlib
import os
import random
import sys
import time
from collections import defaultdict

BASE_DIR = os.path.dirname(__file__)
sys.path.insert(0, BASE_DIR)

from astrbot.api import logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.event.filter import EventMessageType
import astrbot.api.message_components as Comp
from astrbot.api.star import Context, Star, register

from services.tts_service import TTSService
from database.meme_db import MemeDB
from utils.meme_collector import MemeCollector
from utils.image import image_from_path
from core.meme.analyzer import MemeAnalyzer
from core.meme.generator import MemeImageGenerator

# ── 数据目录 ──────────────────────────────────────────────────────────
_DATA_DIR = os.path.join(BASE_DIR, "data")
_MEMES_DIR = os.path.join(_DATA_DIR, "memes")      # 原始收集的图片
_AI_MEMES_DIR = os.path.join(_DATA_DIR, "ai_memes")  # AI 生成的图片
_DB_PATH = os.path.join(_DATA_DIR, "memes.db")

for _d in (_DATA_DIR, _MEMES_DIR, _AI_MEMES_DIR):
    os.makedirs(_d, exist_ok=True)

# 触发 AI 生图的关键词（群聊中说这些词会直接生成，跳过概率门控）
_MEME_KEYWORDS = (
    "发个表情包", "来个表情包", "整个表情包", "给我表情包",
    "发个梗图", "来个梗图", "整个梗图",
    "生成表情包", "合成表情包", "生成梗图",
)


@register("astrbot_plugin_meme_generate", "huaziqi", "TTS 语音合成 & 表情包插件", "1.1.0")
class MyPlugin(Star):

    def __init__(self, context: Context):
        super().__init__(context)

        # TTS
        self.tts_service = TTSService()

        # 表情包系统
        self.db = MemeDB(_DB_PATH)
        self.collector = MemeCollector(_MEMES_DIR)
        self.analyzer = MemeAnalyzer(context)
        self.generator = MemeImageGenerator(_AI_MEMES_DIR)

        # 每个群的最后一次发送时间戳，防止刷屏
        self._last_send: dict[str, float] = defaultdict(float)

        # 冷却时间（秒）。两次表情包发送之间的最小间隔
        self._cooldown = 90

        # 触发检查的概率（每条群消息有 N% 机会触发 AI 语境判断）
        self._check_prob = 0.18

        logger.info(
            f"[Meme] 插件初始化完成，数据库已有 {self.db.count()} 条表情包记录"
        )

    # ──────────────────────────────────────────────────────────────────
    # TTS 指令（保持原有功能）
    # ──────────────────────────────────────────────────────────────────
    @filter.command("tts")
    async def tts(self, event: AstrMessageEvent):
        """将文字转换为语音消息发送。用法：/tts 你好世界"""
        raw = event.message_str.strip()
        parts = raw.split(None, 1)
        text = parts[1].strip() if len(parts) > 1 else ""
        if not text:
            yield event.plain_result("请在命令后输入要转换的文字，例如：/tts 你好世界")
            return
        logger.info(f"[TTS] 正在转换：{text}")
        try:
            wav_path = await self.tts_service.create(text)
            logger.info(f"[TTS] 语音文件：{wav_path}")
            yield event.chain_result([Comp.Record(file=wav_path, url=wav_path)])
        except Exception as e:
            logger.error(f"[TTS] 生成失败：{e}")
            yield event.plain_result(f"❌ 语音生成失败：{e}")

    # ──────────────────────────────────────────────────────────────────
    # /meme 指令：立即 AI 生成并发送一张表情包
    # ──────────────────────────────────────────────────────────────────
    @filter.command("meme")
    async def cmd_meme(self, event: AstrMessageEvent):
        """/meme [描述] — 立即生成一张 AI 表情包并发送。
        有描述词时直接用描述生图；无描述词时根据群聊语境自动生成。
        """
        raw = event.message_str.strip()
        parts = raw.split(None, 1)
        user_desc = parts[1].strip() if len(parts) > 1 else ""

        group_id = event.get_group_id() or event.session_id
        umo = event.unified_msg_origin

        yield event.plain_result("🎨 AI 表情包生成中，稍等…")

        if user_desc:
            # 用户提供了描述 → 直接作为 draw prompt，跳过文案生成
            draw_prompt = (
                f"一张有趣的中文梗图表情包，主题：{user_desc}。"
                "卡通风格，简洁背景，突出幽默感。"
            )
            path = await self.analyzer.draw_image(draw_prompt, _AI_MEMES_DIR)
            if not path:
                # PIL 降级：把描述当上方文字
                logger.info("[Meme] draw provider 失败，PIL 降级渲染")
                path = self.generator.create_text_meme(user_desc[:14], "（AI 生成）", "搞笑")
        else:
            # 无描述 → 走完整流程（语境文案 + draw + PIL 降级）
            path = await self._ai_generate(group_id, ["搞笑", "有趣"], umo)

        if not path:
            yield event.plain_result("❌ 生成失败，请检查 draw provider 配置后重试")
            return

        self._last_send[group_id] = time.time()
        logger.info(f"[Meme] /meme 生成完成: {path}")
        yield event.chain_result([image_from_path(path)])

    # ──────────────────────────────────────────────────────────────────
    # 群消息监听：自动收集 + 自动发送
    # ──────────────────────────────────────────────────────────────────
    @filter.event_message_type(EventMessageType.GROUP_MESSAGE)
    async def handle_group(self, event: AstrMessageEvent):
        """监听所有群消息，静默收集表情包并按语境发送。"""

        # ── 0. 跳过机器人自身发送的消息，避免回声循环 ────────────────
        self_id = event.get_self_id()
        sender_id = event.get_sender_id()
        if self_id and sender_id and str(self_id) == str(sender_id):
            return

        group_id = event.get_group_id() or event.session_id
        umo = event.unified_msg_origin
        messages = event.get_messages()

        # ── 1. 收集消息中的图片 ────────────────────────────────────
        for comp in messages:
            if isinstance(comp, Comp.Image):
                await self._collect(comp, group_id, umo)

        # ── 2. 关键词检测：主动请求直接生成，绕过概率/冷却门控 ──────
        text = event.message_str.strip()
        if text and any(kw in text for kw in _MEME_KEYWORDS):
            path = await self._ai_generate(group_id, ["搞笑", "有趣"], umo)
            if path:
                self._last_send[group_id] = time.time()
                event.call_llm = True
                logger.info(f"[Meme] 关键词触发，向群 {group_id} 发送: {path}")
                yield event.chain_result([image_from_path(path)])
            return  # 无论是否成功，本次处理结束，不再走自动流程

        # ── 3. 记录文本历史（排除指令）────────────────────────────
        if text and not text.startswith("/"):
            self.analyzer.push(group_id, text)

        # ── 4. 冷却检查 ────────────────────────────────────────────
        now = time.time()
        if now - self._last_send[group_id] < self._cooldown:
            return

        # ── 5. 随机概率门控，避免每条消息都触发 AI 判断 ────────────
        if random.random() > self._check_prob:
            return

        # ── 6. AI 判断是否发表情包 ─────────────────────────────────
        should, tags = await self.analyzer.should_send_meme(group_id, umo)
        if not should:
            return

        # ── 7. 选择/生成表情包并发送 ───────────────────────────────
        meme_path, meme_id = await self._pick_or_generate(group_id, tags, umo)
        if not meme_path:
            return

        self._last_send[group_id] = now
        if meme_id is not None:
            self.db.inc_send_count(meme_id)

        # 阻止 AstrBot 默认 LLM 对同一条消息再发一条文字回复
        # pipeline 条件：`and not event.call_llm`，设为 True 即可跳过 LLM
        event.call_llm = True

        logger.info(f"[Meme] 向群 {group_id} 发送表情包: {meme_path}")
        # 用 base64 编码发送，避免 Windows 反斜杠路径在 QQ 适配器中被当成文本
        yield event.chain_result([image_from_path(meme_path)])

    # ──────────────────────────────────────────────────────────────────
    # 内部工具方法
    # ──────────────────────────────────────────────────────────────────
    async def _collect(
        self, img_comp: Comp.Image, group_id: str, umo: str
    ) -> None:
        """下载图片 → 去重检查 → 打标签 → 入库。"""
        result = await self.collector.collect_image(img_comp)
        if result is None:
            return

        file_hash, file_path = result
        if self.db.exists(file_hash):
            return

        # 用视觉模型打标签（失败时空标签也入库）
        tags = await self.analyzer.tag_image(file_path, umo)
        new_id = self.db.insert(file_hash, file_path, group_id, tags)
        if new_id:
            logger.info(
                f"[Meme] 新表情包入库 id={new_id} 群={group_id} 标签={tags}"
            )

    async def _pick_or_generate(
        self, group_id: str, tags: list[str], umo: str
    ) -> tuple[str | None, int | None]:
        """从数据库选一张或 AI 生成一张。返回 (file_path, meme_id_or_None)。"""
        db_count = self.db.count()
        use_db = db_count > 0 and random.random() < 0.70

        if use_db:
            candidates = self.db.find_by_tags(tags, limit=8)
            if not candidates:
                candidates = self.db.random_memes(5)
            if candidates:
                chosen = random.choice(candidates)
                return chosen["file_path"], chosen["id"]

        # AI 生成
        return await self._ai_generate(group_id, tags, umo), None

    async def _ai_generate(
        self, group_id: str, tags: list[str], umo: str
    ) -> str | None:
        """生成 AI 表情包，返回图片路径；失败返回 None。

        优先级：
          1. draw provider → 调用 images.generate 接口（DALL-E 3 / SD 等）
          2. 降级 → PIL 本地渲染（无需外部 API）
        """
        # ── 1. 让文字模型生成文案 ─────────────────────────────────────
        content = await self.analyzer.generate_meme_text(group_id, umo)
        if not content:
            return None

        top   = content.get("top", "")
        bottom = content.get("bottom", "")
        style  = content.get("style", "default")

        if not top and not bottom:
            return None

        # ── 2. 尝试 AI 生图（draw provider）──────────────────────────
        draw_prompt = (
            f"一张有趣的中文梗图表情包，图片上方写着：「{top}」，"
            f"下方写着：「{bottom}」。卡通风格，简洁背景，突出文字，"
            f"整体情绪：{style}。"
        )
        path = await self.analyzer.draw_image(draw_prompt, _AI_MEMES_DIR)

        # ── 3. 降级：本地 PIL 渲染 ────────────────────────────────────
        if not path:
            logger.info("[Meme] AI 生图不可用，降级为本地 PIL 渲染")
            path = self.generator.create_text_meme(top, bottom, style)

        if not path:
            return None

        # ── 4. 入库 ───────────────────────────────────────────────────
        try:
            with open(path, "rb") as f:
                h = hashlib.md5(f.read()).hexdigest()
            ai_tags = list({style} | set(tags))
            self.db.insert(h, path, group_id, ai_tags, is_ai_generated=True)
        except Exception as e:
            logger.warning(f"[Meme] AI 表情包入库失败: {e}")

        return path

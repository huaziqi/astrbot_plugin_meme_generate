from __future__ import annotations

import json
import random
import shutil
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register


@register("meme_generate", "Codex", "群聊表情包保存、导入、氛围匹配与发送", "0.1.0")
class MemeGeneratePlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.base_dir = Path("data/meme_generate")
        self.images_dir = self.base_dir / "images"
        self.index_file = self.base_dir / "index.json"
        self._mood_keywords: Dict[str, List[str]] = {
            "happy": ["哈哈", "开心", "笑", "牛", "赞", "好耶", "赢", "6"],
            "angry": ["气", "怒", "烦", "无语", "离谱", "服了"],
            "sad": ["哭", "难过", "emo", "伤心", "惨", "寄"],
            "surprised": ["震惊", "卧槽", "居然", "离谱", "啊", "逆天"],
            "awkward": ["尴尬", "呃", "？", "...", "沉默"],
        }

    async def initialize(self):
        self.images_dir.mkdir(parents=True, exist_ok=True)
        if not self.index_file.exists():
            self._save_index([])
        logger.info("meme_generate plugin initialized")

    @filter.command("meme_help")
    async def meme_help(self, event: AstrMessageEvent):
        """查看插件帮助"""
        yield event.plain_result(
            "可用命令：\n"
            "/meme_save [mood]\n"
            "/meme_import [mood] [url]\n"
            "/meme_send [可选文本氛围]\n"
            "/meme_list"
        )

    @filter.command("meme_save")
    async def meme_save(self, event: AstrMessageEvent):
        """保存当前消息中的图片到本地表情包库"""
        args = event.message_str.split(maxsplit=1)
        mood = args[1].strip().lower() if len(args) > 1 else self._infer_mood("")
        image_url = self._extract_first_image_url(event)
        if not image_url:
            yield event.plain_result("未在当前消息中找到图片，请在带图消息中使用 /meme_save [mood]。")
            return

        saved = self._save_from_url(image_url, mood, source="group")
        if not saved:
            yield event.plain_result("图片保存失败，请稍后重试。")
            return

        yield event.plain_result(f"保存成功：mood={mood}，id={saved['id']}")

    @filter.command("meme_import")
    async def meme_import(self, event: AstrMessageEvent):
        """手动导入图片 URL 到表情包库"""
        parts = event.message_str.split()
        if len(parts) < 3:
            yield event.plain_result("用法：/meme_import [mood] [url]")
            return

        mood = parts[1].lower().strip()
        url = parts[2].strip()
        saved = self._save_from_url(url, mood, source="import")
        if not saved:
            yield event.plain_result("导入失败：URL 无法下载或格式不受支持。")
            return

        yield event.plain_result(f"导入成功：mood={mood}，id={saved['id']}")

    @filter.command("meme_list")
    async def meme_list(self, event: AstrMessageEvent):
        """查看库存摘要"""
        index = self._load_index()
        if not index:
            yield event.plain_result("当前还没有任何表情包。")
            return
        counts: Dict[str, int] = {}
        for item in index:
            counts[item["mood"]] = counts.get(item["mood"], 0) + 1
        text = "\n".join([f"{k}: {v}" for k, v in sorted(counts.items(), key=lambda x: x[0])])
        yield event.plain_result(f"库存总数：{len(index)}\n{text}")

    @filter.command("meme_send")
    async def meme_send(self, event: AstrMessageEvent):
        """根据聊天氛围发送匹配表情包"""
        payload = event.message_str.replace("/meme_send", "", 1).strip()
        mood = self._infer_mood(payload)
        candidate = self._pick_meme(mood)
        if not candidate:
            yield event.plain_result(f"当前 mood={mood} 没有库存，可先使用 /meme_save 或 /meme_import。")
            return

        image_path = str((self.base_dir / candidate["path"]).resolve())
        sent = await self._send_image(event, image_path)
        if sent:
            yield sent
            return

        yield event.plain_result(
            f"已匹配 mood={mood}，但当前适配器不支持本插件的自动发图接口。图片路径：{candidate['path']}"
        )

    async def _send_image(self, event: AstrMessageEvent, image_path: str):
        # 兼容不同 AstrBot 适配器：优先调用可能存在的图片结果方法
        if hasattr(event, "image_result"):
            return event.image_result(image_path)
        if hasattr(event, "file_result"):
            return event.file_result(image_path)
        return None

    def _save_from_url(self, url: str, mood: str, source: str) -> Optional[Dict[str, str]]:
        try:
            import requests

            resp = requests.get(url, timeout=20)
            if resp.status_code != 200:
                return None
            ext = self._guess_ext_from_url(url)
            ts = int(time.time() * 1000)
            filename = f"{ts}_{random.randint(1000, 9999)}.{ext}"
            target = self.images_dir / filename
            target.write_bytes(resp.content)

            item = {
                "id": f"meme_{ts}",
                "path": str(Path("images") / filename),
                "mood": mood,
                "source": source,
                "created_at": ts,
                "url": url,
            }
            index = self._load_index()
            index.append(item)
            self._save_index(index)
            return item
        except Exception as e:
            logger.error(f"save meme failed: {e}")
            return None

    def _extract_first_image_url(self, event: AstrMessageEvent) -> Optional[str]:
        messages = event.get_messages()
        for msg in messages:
            s = str(msg)
            if "http" in s and any(x in s.lower() for x in [".png", ".jpg", ".jpeg", ".gif", ".webp"]):
                return s.split("http", 1)[1].split()[0].join(["http"])
            if hasattr(msg, "url"):
                return getattr(msg, "url")
        return None

    def _guess_ext_from_url(self, url: str) -> str:
        low = url.lower()
        for ext in ["png", "jpg", "jpeg", "gif", "webp"]:
            if f".{ext}" in low:
                return ext
        return "png"

    def _infer_mood(self, text: str) -> str:
        content = text.strip()
        if not content:
            return "happy"
        score: Dict[str, int] = {k: 0 for k in self._mood_keywords.keys()}
        for mood, words in self._mood_keywords.items():
            for w in words:
                if w in content:
                    score[mood] += 1
        mood = max(score.items(), key=lambda x: x[1])[0]
        return mood if score[mood] > 0 else "happy"

    def _pick_meme(self, mood: str) -> Optional[Dict[str, str]]:
        index = self._load_index()
        matched = [x for x in index if x.get("mood") == mood]
        if not matched:
            matched = index
        return random.choice(matched) if matched else None

    def _load_index(self) -> List[Dict[str, str]]:
        if not self.index_file.exists():
            return []
        try:
            return json.loads(self.index_file.read_text(encoding="utf-8"))
        except Exception:
            backup = self.index_file.with_suffix(".bak")
            shutil.copyfile(self.index_file, backup)
            return []

    def _save_index(self, data: List[Dict[str, str]]) -> None:
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.index_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    async def terminate(self):
        logger.info("meme_generate plugin terminated")

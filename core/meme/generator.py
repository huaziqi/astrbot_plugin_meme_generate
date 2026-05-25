"""用 PIL 生成纯文字梗图。无需外部 API，离线可用。"""

import os
import uuid

from astrbot.api import logger

try:
    from PIL import Image, ImageDraw, ImageFont

    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False


# 不同风格的背景色（RGB）
_STYLE_BG: dict[str, tuple[int, int, int]] = {
    "搞笑": (255, 252, 196),
    "无语": (215, 215, 215),
    "开心": (255, 236, 196),
    "悲伤": (196, 210, 255),
    "愤怒": (255, 196, 196),
    "惊讶": (196, 255, 230),
    "可爱": (255, 196, 230),
    "加油": (215, 255, 196),
    "委屈": (220, 220, 255),
    "得意": (255, 240, 160),
    "吃瓜": (196, 255, 196),
    "打工": (230, 230, 230),
    "摸鱼": (196, 240, 255),
}
_DEFAULT_BG = (255, 255, 255)


class MemeImageGenerator:
    """生成文字梗图并保存为 PNG 文件。"""

    _CANDIDATE_FONTS = [
        # Windows
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\simsun.ttc",
        r"C:\Windows\Fonts\simkai.ttf",
        # Linux
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    ]

    def __init__(self, output_dir: str):
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)
        self._font_path: str | None = self._detect_font()

    def _detect_font(self) -> str | None:
        for p in self._CANDIDATE_FONTS:
            if os.path.exists(p):
                return p
        return None

    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------
    def create_text_meme(
        self,
        top_text: str,
        bottom_text: str,
        style: str = "default",
        width: int = 500,
        height: int = 340,
    ) -> str | None:
        """生成文字梗图，返回 PNG 文件绝对路径；失败返回 None。"""
        if not PIL_AVAILABLE:
            logger.warning("[Meme] Pillow 未安装，无法生成 AI 表情包。请 pip install Pillow")
            return None

        try:
            bg = _STYLE_BG.get(style, _DEFAULT_BG)
            img = Image.new("RGB", (width, height), bg)
            draw = ImageDraw.Draw(img)

            # ---------- 字体 ----------
            font_big = self._load_font(44)
            font_small = self._load_font(34)
            font_wm = self._load_font(16)

            # ---------- 边框 ----------
            draw.rectangle([4, 4, width - 5, height - 5], outline=(30, 30, 30), width=3)

            # ---------- 分隔线 ----------
            mid_y = height // 2
            draw.line([(10, mid_y), (width - 10, mid_y)], fill=(180, 180, 180), width=1)

            # ---------- 上方文字（黑色）----------
            if top_text:
                self._draw_text_block(
                    draw, top_text, font_big,
                    area=(10, 10, width - 10, mid_y - 6),
                    color=(20, 20, 20),
                )

            # ---------- 下方文字（深红）----------
            if bottom_text:
                self._draw_text_block(
                    draw, bottom_text, font_small,
                    area=(10, mid_y + 6, width - 10, height - 28),
                    color=(160, 20, 20),
                )

            # ---------- 水印 ----------
            draw.text((width - 88, height - 22), "AI表情包", fill=(180, 180, 180), font=font_wm)

            out_path = os.path.join(self.output_dir, f"ai_{uuid.uuid4().hex[:10]}.png")
            img.save(out_path, "PNG")
            logger.info(f"[Meme] 生成 AI 表情包: {out_path}")
            return out_path

        except Exception as e:
            logger.error(f"[Meme] 生成表情包失败: {e}")
            return None

    # ------------------------------------------------------------------
    # 功能二：给现有图片叠加文字（白字黑边，Impact 风格）
    # ------------------------------------------------------------------
    def add_text_to_meme(
        self,
        base_image_path: str,
        top_text: str,
        bottom_text: str,
    ) -> str | None:
        """打开已有表情包，在顶部/底部叠加白字黑边文字，返回新图片路径。"""
        if not PIL_AVAILABLE:
            logger.warning("[Meme] Pillow 未安装，无法叠加文字")
            return None
        try:
            img = Image.open(base_image_path).convert("RGB")
            w, h = img.size

            # 缩放：太大压小，太小放大，保证文字清晰
            max_side = 800
            if max(w, h) > max_side:
                scale = max_side / max(w, h)
                img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
            elif max(w, h) < 300:
                scale = 300 / max(w, h)
                img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
            w, h = img.size

            draw = ImageDraw.Draw(img)
            font_size = max(22, w // 13)
            font = self._load_font(font_size)

            if top_text:
                self._draw_outlined_text(draw, top_text, font, w, h, "top")
            if bottom_text:
                self._draw_outlined_text(draw, bottom_text, font, w, h, "bottom")

            out_path = os.path.join(self.output_dir, f"overlay_{uuid.uuid4().hex[:10]}.png")
            img.save(out_path, "PNG")
            logger.info(f"[Meme] 文字叠加完成: {out_path}")
            return out_path
        except Exception as e:
            logger.error(f"[Meme] 文字叠加失败: {e}")
            return None

    def _draw_outlined_text(
        self,
        draw: "ImageDraw.ImageDraw",
        text: str,
        font,
        img_w: int,
        img_h: int,
        position: str = "top",
        stroke: int = 2,
    ) -> None:
        """在图片顶部或底部绘制带黑色描边的白色文字。"""
        char_w = max(1, getattr(font, "size", 24))
        chars_per_line = max(8, img_w // char_w)

        lines: list[str] = []
        tmp = text
        while len(tmp) > chars_per_line:
            lines.append(tmp[:chars_per_line])
            tmp = tmp[chars_per_line:]
        if tmp:
            lines.append(tmp)

        font_size = getattr(font, "size", 24)
        line_h = font_size + 4
        total_h = len(lines) * line_h
        margin = 6
        start_y = margin if position == "top" else img_h - total_h - margin

        for i, line in enumerate(lines):
            try:
                bbox = draw.textbbox((0, 0), line, font=font)
                text_w = bbox[2] - bbox[0]
            except AttributeError:
                text_w = len(line) * font_size

            x = max(0, (img_w - text_w) // 2)
            y = start_y + i * line_h

            # 8 方向描边
            for dx in range(-stroke, stroke + 1):
                for dy in range(-stroke, stroke + 1):
                    if dx == 0 and dy == 0:
                        continue
                    draw.text((x + dx, y + dy), line, fill=(0, 0, 0), font=font)
            # 白色主文字
            draw.text((x, y), line, fill=(255, 255, 255), font=font)

    # ------------------------------------------------------------------
    # 功能三：两张表情包左右拼合
    # ------------------------------------------------------------------
    def combine_memes(
        self,
        image1_path: str,
        image2_path: str,
    ) -> str | None:
        """将两张表情包左右拼合成一张，返回新图片路径。"""
        if not PIL_AVAILABLE:
            logger.warning("[Meme] Pillow 未安装，无法拼合图片")
            return None
        try:
            img1 = Image.open(image1_path).convert("RGB")
            img2 = Image.open(image2_path).convert("RGB")

            # 统一高度（取较小值，上限 400，下限 150）
            target_h = max(150, min(img1.height, img2.height, 400))

            def fit_height(img: "Image.Image", h: int) -> "Image.Image":
                ratio = h / img.height
                return img.resize((max(1, int(img.width * ratio)), h), Image.LANCZOS)

            def cap_width(img: "Image.Image", max_w: int = 400) -> "Image.Image":
                if img.width <= max_w:
                    return img
                ratio = max_w / img.width
                return img.resize((max_w, max(1, int(img.height * ratio))), Image.LANCZOS)

            img1 = cap_width(fit_height(img1, target_h))
            img2 = cap_width(fit_height(img2, target_h))

            # 宽度压缩后高度可能略有差异，再对齐一次
            final_h = min(img1.height, img2.height)
            if img1.height != final_h:
                img1 = fit_height(img1, final_h)
            if img2.height != final_h:
                img2 = fit_height(img2, final_h)

            gap = 6
            canvas = Image.new("RGB", (img1.width + gap + img2.width, final_h), (30, 30, 30))
            canvas.paste(img1, (0, 0))
            canvas.paste(img2, (img1.width + gap, 0))

            out_path = os.path.join(self.output_dir, f"combo_{uuid.uuid4().hex[:10]}.png")
            canvas.save(out_path, "PNG")
            logger.info(f"[Meme] 拼合完成: {out_path}")
            return out_path
        except Exception as e:
            logger.error(f"[Meme] 拼合失败: {e}")
            return None

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    def _load_font(self, size: int):
        if self._font_path:
            try:
                return ImageFont.truetype(self._font_path, size)
            except Exception:
                pass
        return ImageFont.load_default()

    def _draw_text_block(
        self,
        draw: "ImageDraw.ImageDraw",
        text: str,
        font,
        area: tuple[int, int, int, int],
        color: tuple[int, int, int],
    ):
        """在给定矩形区域内居中绘制（自动截断长文字）。"""
        x1, y1, x2, y2 = area
        max_w = x2 - x1

        # 按字符数简单换行（每行最多 12 个字）
        chars_per_line = 12
        lines: list[str] = []
        while len(text) > chars_per_line:
            lines.append(text[:chars_per_line])
            text = text[chars_per_line:]
        if text:
            lines.append(text)

        font_size = getattr(font, "size", 30)
        line_h = font_size + 6
        total_h = len(lines) * line_h
        start_y = y1 + ((y2 - y1) - total_h) // 2

        for i, line in enumerate(lines):
            # 计算文字宽度
            try:
                bbox = draw.textbbox((0, 0), line, font=font)
                text_w = bbox[2] - bbox[0]
            except AttributeError:
                text_w = len(line) * font_size

            lx = x1 + (max_w - text_w) // 2
            ly = start_y + i * line_h

            # 阴影
            draw.text((lx + 2, ly + 2), line, fill=(200, 200, 200), font=font)
            # 正文
            draw.text((lx, ly), line, fill=color, font=font)

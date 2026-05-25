"""图片工具函数。"""

import base64

import astrbot.api.message_components as Comp


def image_from_path(path: str) -> Comp.Image:
    """将本地图片文件读取为 base64 Image 组件，绕过 Windows 路径兼容问题。"""
    try:
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        return Comp.Image.fromBase64(b64)
    except Exception:
        # 兜底：仍尝试 fromFileSystem（用正斜杠）
        return Comp.Image.fromFileSystem(path.replace("\\", "/"))

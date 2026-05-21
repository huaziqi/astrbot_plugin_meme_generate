"""负责从消息组件中提取并持久化图片。"""

import hashlib
import os
import shutil

from astrbot.api import logger
from astrbot.api.message_components import Image as ImageComp


class MemeCollector:
    def __init__(self, storage_dir: str):
        self.storage_dir = storage_dir
        os.makedirs(storage_dir, exist_ok=True)

    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------
    async def collect_image(self, img_comp: ImageComp) -> tuple[str, str] | None:
        """从消息 Image 组件下载图片，持久化到本地。

        Returns:
            (file_hash, file_path) 或 None（失败时）。
        """
        try:
            local_path = await img_comp.convert_to_file_path()
        except Exception as e:
            logger.warning(f"[Meme] 获取图片路径失败: {e}")
            return None

        try:
            with open(local_path, "rb") as f:
                data = f.read()
        except Exception as e:
            logger.warning(f"[Meme] 读取图片数据失败: {e}")
            return None

        file_hash = hashlib.md5(data).hexdigest()
        ext = os.path.splitext(local_path)[1].lower() or ".jpg"
        dest = os.path.join(self.storage_dir, f"{file_hash}{ext}")

        if not os.path.exists(dest):
            try:
                shutil.copy2(local_path, dest)
            except Exception as e:
                logger.warning(f"[Meme] 保存图片失败: {e}")
                return None

        return file_hash, dest

    @staticmethod
    def hash_file(path: str) -> str:
        with open(path, "rb") as f:
            return hashlib.md5(f.read()).hexdigest()

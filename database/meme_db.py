"""SQLite 存储层，管理收集到的表情包元数据。"""

import json
import os
import sqlite3
from datetime import datetime


class MemeDB:
    def __init__(self, db_path: str):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.db_path = db_path
        self._init_db()

    # ------------------------------------------------------------------
    # 初始化
    # ------------------------------------------------------------------
    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memes (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_hash       TEXT    UNIQUE NOT NULL,
                    file_path       TEXT    NOT NULL,
                    source_group    TEXT    DEFAULT '',
                    tags            TEXT    DEFAULT '[]',
                    created_at      TEXT    DEFAULT '',
                    send_count      INTEGER DEFAULT 0,
                    is_ai_generated INTEGER DEFAULT 0
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_group ON memes(source_group)"
            )
            conn.commit()

    # ------------------------------------------------------------------
    # 写操作
    # ------------------------------------------------------------------
    def insert(
        self,
        file_hash: str,
        file_path: str,
        source_group: str = "",
        tags: list | None = None,
        is_ai_generated: bool = False,
    ) -> int | None:
        """插入新表情包记录，hash 已存在时忽略。返回新行 id（忽略时返回 None）。"""
        tags = tags or []
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO memes "
                "(file_hash, file_path, source_group, tags, created_at, is_ai_generated) "
                "VALUES (?,?,?,?,?,?)",
                (
                    file_hash,
                    file_path,
                    source_group,
                    json.dumps(tags, ensure_ascii=False),
                    datetime.now().isoformat(),
                    1 if is_ai_generated else 0,
                ),
            )
            conn.commit()
            return cur.lastrowid if cur.rowcount else None

    def inc_send_count(self, meme_id: int):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE memes SET send_count=send_count+1 WHERE id=?", (meme_id,)
            )
            conn.commit()

    # ------------------------------------------------------------------
    # 读操作
    # ------------------------------------------------------------------
    def exists(self, file_hash: str) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            return (
                conn.execute(
                    "SELECT 1 FROM memes WHERE file_hash=?", (file_hash,)
                ).fetchone()
                is not None
            )

    def find_by_tags(self, tags: list[str], limit: int = 10) -> list[dict]:
        """按标签模糊搜索，返回文件存在的结果列表。"""
        if not tags:
            return []
        seen: set[int] = set()
        result: list[dict] = []
        with sqlite3.connect(self.db_path) as conn:
            for tag in tags:
                rows = conn.execute(
                    "SELECT id, file_path, tags, source_group FROM memes "
                    "WHERE tags LIKE ? ORDER BY send_count ASC LIMIT ?",
                    (f"%{tag}%", limit),
                ).fetchall()
                for r in rows:
                    if r[0] not in seen and os.path.exists(r[1]):
                        seen.add(r[0])
                        result.append(
                            {
                                "id": r[0],
                                "file_path": r[1],
                                "tags": json.loads(r[2]),
                                "source_group": r[3],
                            }
                        )
        return result[:limit]

    def random_memes(self, limit: int = 5) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT id, file_path, tags FROM memes ORDER BY RANDOM() LIMIT ?",
                (limit * 2,),  # 多取一些，过滤掉已删除的文件
            ).fetchall()
        return [
            {"id": r[0], "file_path": r[1], "tags": json.loads(r[2])}
            for r in rows
            if os.path.exists(r[1])
        ][:limit]

    def count(self) -> int:
        with sqlite3.connect(self.db_path) as conn:
            return conn.execute("SELECT COUNT(*) FROM memes").fetchone()[0]

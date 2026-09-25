"""内存数据仓库：给每个业务模块准备一份可筛选、可流转的示例数据。

真实项目里这里会换成数据库访问层；当前实现只依赖标准库，保证克隆下来就能起。
"""
from __future__ import annotations

import threading
from typing import Any

from app.seed import SEED_ROWS

# 仅供业务服务内部读写的流水/台账表，不在运营看板中单独计数
INTERNAL_TABLES = {"crew_history"}


class Store:
    def __init__(self) -> None:
        self._tables: dict[str, list[dict[str, Any]]] = {
            name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()
        }
        # 多经办人同时办理同一成员时串行化，杜绝重复离场等并发覆盖
        self._locks: dict[str, threading.RLock] = {}
        self._locks_guard = threading.Lock()

    def lock_for(self, key: str) -> threading.RLock:
        """按业务键取一把可重入锁：同一成员的进场/离场/请假串行，不同成员互不阻塞。"""
        with self._locks_guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = threading.RLock()
                self._locks[key] = lock
            return lock

    def append(self, module: str, row: dict[str, Any]) -> dict[str, Any]:
        """追加一条流水记录；自增 id 由仓库统一分配，避免业务侧重复编号。"""
        rows = self.rows(module)
        row = dict(row)
        row["id"] = max((int(item.get("id", 0)) for item in rows), default=0) + 1
        rows.append(row)
        return row

    def module_names(self) -> list[str]:
        return sorted(self._tables)

    def rows(self, module: str) -> list[dict[str, Any]]:
        return self._tables.setdefault(module, [])

    def find(self, module: str, entry_id: int) -> dict[str, Any] | None:
        for row in self.rows(module):
            if int(row.get("id", 0)) == entry_id:
                return row
        return None

    def overview(self) -> dict[str, object]:
        # crew_history 是剧组权限台账的流水表，只追加不改写，不计入业务看板统计
        modules: list[dict[str, object]] = []
        for name in self.module_names():
            if name in INTERNAL_TABLES:
                continue
            rows = self.rows(name)
            modules.append({
                "name": name,
                "created": len(rows),
                "pending": sum(1 for row in rows if row.get("pending")),
                "abnormal": sum(1 for row in rows if row.get("abnormal")),
            })
        cards = [
            {"label": "业务模块", "value": len(modules)},
            {"label": "今日新增", "value": sum(int(item["created"]) for item in modules)},
            {"label": "待处理", "value": sum(int(item["pending"]) for item in modules)},
            {"label": "异常量", "value": sum(int(item["abnormal"]) for item in modules)},
        ]
        return {"cards": cards, "modules": modules}


store = Store()

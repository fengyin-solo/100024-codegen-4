"""剧组人员业务规则：岗位权限归属、进出/请假状态流转与台账口径都收在这里。

设计要点：
- 岗位职务决定经办人能办什么：制片可办进场/离场/请假，场务只能办进场，
  演员统筹只能办请假；其他岗位（摄影师、灯光助理等）一律只读。
- 人员列表、在组统计、权限台账全部从 store 里的同一份 crew 表现算，
  成员归属（岗位/组别）调整后各处结果天然一致，不存在多份副本。
- 进场/离场/请假/归属变更每次成功都追加一条 crew_history 流水，
  历史记录只追加不改写，旧记录保持原样。
- 同一成员的动作在 per-member 锁内完成“先校验后落库”，多人同时办理离场
  只有第一个成功，其余按重复离场拒绝。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.store import store

MODULE = "crew"
HISTORY_MODULE = "crew_history"

REQUIRED_FIELDS = ["成员编号", "姓名", "岗位职务"]

STATUS_PENDING = "待进场"
STATUS_ON_SET = "在组"
STATUS_LEFT = "已离场"
STATUS_LEAVE = "已请假"
STATUS_ORDER = [STATUS_PENDING, STATUS_ON_SET, STATUS_LEFT, STATUS_LEAVE]

ACTION_CHECK_IN = "办理进场"
ACTION_CHECK_OUT = "办理离场"
ACTION_LEAVE = "登记请假"
ACTION_REASSIGN = "归属变更"
ACTIONS = [ACTION_CHECK_IN, ACTION_CHECK_OUT, ACTION_LEAVE]

# 岗位职务按包含关键词归类；除下列岗位外的成员全部按只读处理
ROLE_PRODUCER = "制片"
ROLE_STAGEHAND = "场务"
ROLE_CASTING_COORDINATOR = "演员统筹"
ROLE_READONLY = "只读成员"

# 岗位 -> 可办理动作
PERMISSION_MATRIX: dict[str, set[str]] = {
    ROLE_PRODUCER: {ACTION_CHECK_IN, ACTION_CHECK_OUT, ACTION_LEAVE},
    ROLE_STAGEHAND: {ACTION_CHECK_IN},
    ROLE_CASTING_COORDINATOR: {ACTION_LEAVE},
    ROLE_READONLY: set(),
}
ROLE_LABELS = {
    ROLE_PRODUCER: "制片（进场/离场/请假均可办理）",
    ROLE_STAGEHAND: "场务（仅可办理进场）",
    ROLE_CASTING_COORDINATOR: "演员统筹（仅可办理请假）",
    ROLE_READONLY: "只读成员（不能改动）",
}

# 每个动作允许的源状态：不在表里的一律拒绝，防止待进场直接离场、重复离场等
ACTION_SOURCE_STATUSES: dict[str, set[str]] = {
    ACTION_CHECK_IN: {STATUS_PENDING},
    ACTION_CHECK_OUT: {STATUS_ON_SET, STATUS_LEAVE},
    ACTION_LEAVE: {STATUS_ON_SET},
}


class PermissionError(Exception):
    """经办人岗位不允许办理该动作（只读或越权）。"""


class ConflictError(Exception):
    """状态不允许该动作，如重复离场、待进场请假等。"""


class CrewService:
    # ---------- 查询：列表 / 详情 ----------

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        group: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = self._snapshot_rows()
        if keyword:
            rows = [
                row for row in rows
                if keyword in str(row.get("成员编号", "")) or keyword in str(row.get("姓名", ""))
            ]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if group:
            rows = [row for row in rows if group in str(row.get("所属组别", ""))]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        row = store.find(MODULE, entry_id)
        return self._present(row) if row else None

    # ---------- 统一台账：权限归属 + 在组统计 + 流水，全部来自同一份数据 ----------

    def ledger(self) -> dict[str, Any]:
        rows = self._snapshot_rows()
        entries = []
        for row in rows:
            role = self.role_of(row.get("岗位职务"))
            entries.append({
                "id": row.get("id"),
                "成员编号": row.get("成员编号", ""),
                "姓名": row.get("姓名", ""),
                "岗位职务": row.get("岗位职务", ""),
                "所属组别": row.get("所属组别", ""),
                "在组状态": row.get("status", ""),
                "权限角色": role,
                "权限说明": ROLE_LABELS[role],
                "可办理动作": sorted(PERMISSION_MATRIX[role]),
                "只读": role == ROLE_READONLY,
            })
        return {
            "权限矩阵": {
                role: {"角色": ROLE_LABELS[role], "可办理动作": sorted(actions)}
                for role, actions in PERMISSION_MATRIX.items()
            },
            "在组统计": self.stats(),
            "成员台账": entries,
            "进出记录": self.history(),
        }

    def stats(self) -> dict[str, Any]:
        rows = store.rows(MODULE)
        on_set = sum(1 for row in rows if row.get("status") == STATUS_ON_SET)
        pending = sum(1 for row in rows if row.get("status") == STATUS_PENDING)
        on_leave = sum(1 for row in rows if row.get("status") == STATUS_LEAVE)
        left = sum(1 for row in rows if row.get("status") == STATUS_LEFT)
        groups: dict[str, int] = {}
        for row in rows:
            if row.get("status") == STATUS_ON_SET:
                group = str(row.get("所属组别") or "未分组")
                groups[group] = groups.get(group, 0) + 1
        return {
            "在组人数": on_set,
            "待进场人员": pending,
            "今日请假": on_leave,
            "已离场人数": left,
            "成员总数": len(rows),
            "各组在组": groups,
        }

    def history(
        self,
        *,
        keyword: str | None = None,
        action: str | None = None,
        page: int = 1,
        size: int = 50,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = [dict(row) for row in store.rows(HISTORY_MODULE)]
        if keyword:
            rows = [
                row for row in rows
                if keyword in str(row.get("成员编号", ""))
                or keyword in str(row.get("姓名", ""))
            ]
        if action:
            rows = [row for row in rows if row.get("动作类型") == action]
        total = len(rows)
        # 流水按 id（即追加先后）倒序，最新办理排最前；底层记录顺序不动
        rows.sort(key=lambda item: int(item.get("id", 0)), reverse=True)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    # ---------- 写入：登记成员、办理动作、调整归属 ----------

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        for field in ["成员编号", "姓名", "岗位职务", "所属组别", "联系电话"]:
            entry[field] = str(values.get(field) or "").strip()
        entry["进场日期"] = str(values.get("进场日期") or "").strip()
        entry["离场日期"] = ""
        entry["status"] = STATUS_PENDING
        entry["在组状态"] = STATUS_PENDING
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return self._present(entry), []

    def run_action(
        self,
        entry_id: int,
        action: str,
        *,
        operator_id: int | None = None,
        remark: str | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        operator = self._load_operator(operator_id)
        # 未指定经办人时不允许改动，避免越权请求绕过岗位归属
        if operator is None:
            raise PermissionError("未指定办理人，无法校验岗位权限；请在请求头 X-Operator-Id 或参数 operatorId 中携带经办人成员编号")
        if action not in ACTIONS:
            return None, f"动作「{action}」不属于剧组人员可执行范围"
        role = self.role_of(operator.get("岗位职务"))
        if action not in PERMISSION_MATRIX[role]:
            if role == ROLE_READONLY:
                raise PermissionError(
                    f"经办人「{operator.get('姓名')}」岗位为{operator.get('岗位职务')}，属于只读成员，不能办理「{action}」"
                )
            raise PermissionError(
                f"越权请求已拒绝：{ROLE_LABELS[role]}，无权办理「{action}」"
            )

        # 同一成员的进出/请假串行：先在锁内复查状态，再落库并追加流水
        lock = store.lock_for(f"{MODULE}:{entry_id}")
        with lock:
            entry = store.find(MODULE, entry_id)
            if entry is None:
                return None, f"剧组成员 {entry_id} 不存在或已归档"
            current = str(entry.get("status") or "")
            if current not in ACTION_SOURCE_STATUSES[action]:
                raise ConflictError(self._conflict_message(action, current))

            now = datetime.now()
            today = now.strftime("%Y-%m-%d")
            if action == ACTION_CHECK_IN:
                entry["进场日期"] = today
            elif action == ACTION_CHECK_OUT:
                entry["离场日期"] = today
            entry["status"] = self._target_status(action)
            entry["在组状态"] = entry["status"]
            entry["pending"] = entry["status"] != STATUS_LEFT
            entry["abnormal"] = False

            self._append_history(entry, action, operator, now, remark)
            return self._present(entry), f"剧组成员已{action}"

    def reassign(
        self,
        entry_id: int,
        values: dict[str, Any],
        *,
        operator_id: int | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        """调整成员的岗位职务/所属组别归属；只有制片可办理，历史进出记录不动。"""
        operator = self._load_operator(operator_id)
        if operator is None:
            raise PermissionError("未指定办理人，无法校验岗位权限")
        role = self.role_of(operator.get("岗位职务"))
        if ACTION_REASSIGN not in self._manage_actions(role):
            raise PermissionError(f"越权请求已拒绝：{ROLE_LABELS[role]}，无权调整成员归属")

        lock = store.lock_for(f"{MODULE}:{entry_id}")
        with lock:
            entry = store.find(MODULE, entry_id)
            if entry is None:
                return None, f"剧组成员 {entry_id} 不存在或已归档"
            new_role_title = str(values.get("岗位职务") or "").strip()
            new_group = str(values.get("所属组别") or "").strip()
            if not new_role_title and not new_group:
                return None, "归属调整至少要提供岗位职务或所属组别"
            old_role_title = str(entry.get("岗位职务") or "")
            old_group = str(entry.get("所属组别") or "")
            if new_role_title:
                entry["岗位职务"] = new_role_title
            if new_group:
                entry["所属组别"] = new_group
            now = datetime.now()
            self._append_history(
                entry,
                ACTION_REASSIGN,
                operator,
                now,
                f"岗位：{old_role_title}→{entry.get('岗位职务')}；组别：{old_group}→{entry.get('所属组别')}",
            )
            return self._present(entry), "成员归属已调整，人员列表、在组统计与权限台账已同步"

    # ---------- 内部规则 ----------

    @staticmethod
    def role_of(position: Any) -> str:
        title = str(position or "")
        if ROLE_CASTING_COORDINATOR in title:
            return ROLE_CASTING_COORDINATOR
        if ROLE_STAGEHAND in title:
            return ROLE_STAGEHAND
        if ROLE_PRODUCER in title:
            return ROLE_PRODUCER
        return ROLE_READONLY

    def _manage_actions(self, role: str) -> set[str]:
        # 归属调整属于制片的管理权限，不占用进出/请假三动作的矩阵
        actions = set(PERMISSION_MATRIX[role])
        if role == ROLE_PRODUCER:
            actions.add(ACTION_REASSIGN)
        return actions

    @staticmethod
    def _target_status(action: str) -> str:
        return {
            ACTION_CHECK_IN: STATUS_ON_SET,
            ACTION_CHECK_OUT: STATUS_LEFT,
            ACTION_LEAVE: STATUS_LEAVE,
        }[action]

    @staticmethod
    def _conflict_message(action: str, current: str) -> str:
        if action == ACTION_CHECK_OUT and current == STATUS_LEFT:
            return "重复离场已拒绝：该成员已离场，不能再次办理离场"
        if action == ACTION_CHECK_IN and current == STATUS_ON_SET:
            return "该成员已在组，无需重复办理进场"
        if action == ACTION_CHECK_IN and current == STATUS_LEFT:
            return "该成员已离场，重新进场请先由制片登记新的进场单"
        if action == ACTION_LEAVE and current == STATUS_LEAVE:
            return "该成员已在请假中，不能重复登记请假"
        if action == ACTION_LEAVE and current == STATUS_PENDING:
            return "成员尚未进场，不能登记请假"
        if action == ACTION_CHECK_OUT and current == STATUS_PENDING:
            return "成员尚未进场，不能办理离场"
        return f"当前状态「{current}」不允许办理{action}"

    def _load_operator(self, operator_id: int | None) -> dict[str, Any] | None:
        if operator_id is None:
            return None
        return store.find(MODULE, operator_id)

    def _append_history(
        self,
        entry: dict[str, Any],
        action: str,
        operator: dict[str, Any],
        when: datetime,
        remark: str | None,
    ) -> None:
        store.append(HISTORY_MODULE, {
            "成员编号": entry.get("成员编号", ""),
            "姓名": entry.get("姓名", ""),
            "岗位职务": entry.get("岗位职务", ""),
            "所属组别": entry.get("所属组别", ""),
            "动作类型": action,
            "变更后状态": entry.get("status", ""),
            "办理人": operator.get("姓名", ""),
            "办理人岗位": operator.get("岗位职务", ""),
            "发生时间": when.strftime("%Y-%m-%d %H:%M:%S"),
            "备注": (remark or "").strip(),
        })

    def _snapshot_rows(self) -> list[dict[str, Any]]:
        # 统一经过 _present 输出：列表、台账、统计看到的字段口径完全一致
        return [self._present(row) for row in store.rows(MODULE)]

    @staticmethod
    def _present(row: dict[str, Any]) -> dict[str, Any]:
        data = dict(row)
        data["在组状态"] = row.get("status", "")
        data.setdefault("进场日期", "")
        data.setdefault("离场日期", "")
        return data

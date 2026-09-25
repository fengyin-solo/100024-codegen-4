"""剧组人员业务规则：岗位权限、状态流转、在组统计与权限台账都收在这里。

口径约定：
- 岗位职务决定可办动作：制片可办进场/离场/请假，场务可办进场/离场，
  演员统筹可办进场/请假，其余岗位（导演、演员等）只读，任何改动一律拒绝。
- 进场、离场、请假都要带经办人，经办人必须是名册内的剧组成员；越权请求直接拒绝。
- 状态流转受前置条件约束：待进场才能进场、在组才能离场或请假，
  已离场成员再次离场按“不得重复离场”拒绝。
- 台账（crew_ledger）只追加、不修改、不删除；人员列表、在组统计与台账
  共享 store 里的同一份成员数据，归属一变三处同时更新。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.store import LEDGER_MODULE, store

MODULE = "crew"
REQUIRED_FIELDS = ["成员编号", "姓名", "岗位职务"]

STATUS_PENDING = "待进场"
STATUS_ON_SET = "在组"
STATUS_LEFT = "已离场"
STATUS_ON_LEAVE = "已请假"
STATUS_ORDER = [STATUS_PENDING, STATUS_ON_SET, STATUS_LEFT, STATUS_ON_LEAVE]

ACTION_ENTER = "办理进场"
ACTION_LEAVE = "办理离场"
ACTION_LEAVE_REQUEST = "登记请假"
ACTIONS = [ACTION_ENTER, ACTION_LEAVE, ACTION_LEAVE_REQUEST]

# 岗位职务 → 可办理动作；不在表里的岗位一律只读
ROLE_PERMISSIONS: list[dict[str, Any]] = [
    {"角色": "制片", "岗位关键词": "制片", "可办动作": ACTIONS},
    {"角色": "场务", "岗位关键词": "场务", "可办动作": [ACTION_ENTER, ACTION_LEAVE]},
    {"角色": "演员统筹", "岗位关键词": "演员统筹", "可办动作": [ACTION_ENTER, ACTION_LEAVE_REQUEST]},
]
READONLY_ROLE = "只读成员"

# 动作 → 目标状态，以及允许办理的当前状态
ACTION_RULES: dict[str, dict[str, str]] = {
    ACTION_ENTER: {"target": STATUS_ON_SET, "allow_from": STATUS_PENDING, "date_field": "进场日期"},
    ACTION_LEAVE: {"target": STATUS_LEFT, "allow_from": STATUS_ON_SET, "date_field": "离场日期"},
    ACTION_LEAVE_REQUEST: {"target": STATUS_ON_LEAVE, "allow_from": STATUS_ON_SET, "date_field": ""},
}


def _role_of(position: str) -> dict[str, Any] | None:
    """按岗位职务匹配角色；演员统筹要先于“演员”判断，这里只认三个可办岗位。"""
    for rule in ROLE_PERMISSIONS:
        if rule["岗位关键词"] in position:
            return rule
    return None


def _allowed_actions(position: str) -> list[str]:
    rule = _role_of(position)
    return list(rule["可办动作"]) if rule else []


def _short_action(action: str) -> str:
    return action.removeprefix("办理").removeprefix("登记") or action


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class CrewService:
    # ---- 读取：人员列表 / 明细 / 统计 / 台账，全部来自同一份成员数据 ----

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        with store.crew_lock:
            rows = [self._normalize(dict(row)) for row in store.rows(MODULE)]
        if keyword:
            rows = [
                row
                for row in rows
                if keyword in str(row.get("成员编号", "")) or keyword in str(row.get("姓名", ""))
            ]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        with store.crew_lock:
            entry = store.find(MODULE, entry_id)
            return self._normalize(dict(entry)) if entry else None

    def stats(self) -> dict[str, int]:
        """在组统计与人员列表同口径，归属变化后两边数字永远一致。"""
        with store.crew_lock:
            rows = [self._normalize(dict(row)) for row in store.rows(MODULE)]
            ledger_count = len(store.rows(LEDGER_MODULE))
        return {
            "成员总数": len(rows),
            "在组人数": sum(1 for row in rows if row["status"] == STATUS_ON_SET),
            "待进场人员": sum(1 for row in rows if row["status"] == STATUS_PENDING),
            "已离场人员": sum(1 for row in rows if row["status"] == STATUS_LEFT),
            "请假中人数": sum(1 for row in rows if row["status"] == STATUS_ON_LEAVE),
            "台账记录数": ledger_count,
        }

    def list_ledger(
        self,
        *,
        keyword: str | None = None,
        action: str | None = None,
        result: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = list(store.rows(LEDGER_MODULE))
        if keyword:
            rows = [
                row
                for row in rows
                if keyword in str(row.get("成员编号", ""))
                or keyword in str(row.get("成员姓名", ""))
                or keyword in str(row.get("经办人姓名", ""))
            ]
        if action:
            rows = [row for row in rows if row.get("动作") == action]
        if result:
            rows = [row for row in rows if row.get("结果") == result]
        rows = list(reversed(rows))  # 台账按时间倒序展示，存储顺序永不重排
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def permissions(self, operator_id: str | None = None) -> dict[str, Any]:
        """把岗位权限矩阵和当前经办人的可办动作一起返回，供前端收起越权按钮。"""
        roles = [
            {"岗位职务": rule["角色"], "可办动作": list(rule["可办动作"]), "只读": False}
            for rule in ROLE_PERMISSIONS
        ]
        roles.append({"岗位职务": READONLY_ROLE, "可办动作": [], "只读": True})
        payload: dict[str, Any] = {"actions": ACTIONS, "roles": roles, "operator": None}
        if operator_id:
            operator = self._resolve_operator({"经办人编号": operator_id})
            if operator:
                payload["operator"] = self._operator_view(operator)
        return payload

    # ---- 写入：登记、办理动作；状态只能在这里改 ----

    def missing_fields(self, values: dict[str, Any]) -> list[str]:
        return [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = self.missing_fields(values)
        if missing:
            return None, missing
        with store.crew_lock:
            operator = self._resolve_operator(values)
            if operator is None:
                message = "经办人不在剧组名册内，不得登记成员归属"
                self._append_ledger("归属登记", None, None, False, message, None, None)
                return None, [message]
            if _role_of(str(operator.get("岗位职务", ""))) is None:
                message = f"越权请求已拒绝：{operator.get('姓名')}为只读成员，不能登记成员归属"
                self._append_ledger("归属登记", None, operator, False, message, None, None, denied=True)
                return None, [message]
            rows = store.rows(MODULE)
            entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
            entry.update({field: str(values.get(field)).strip() for field in REQUIRED_FIELDS})
            entry["所属组别"] = str(values.get("所属组别") or "").strip()
            entry["联系电话"] = str(values.get("联系电话") or "").strip()
            entry["进场日期"] = ""
            entry["离场日期"] = ""
            entry["status"] = STATUS_PENDING
            entry["pending"] = True
            entry["abnormal"] = False
            entry["在组状态"] = STATUS_PENDING
            rows.append(entry)
            self._append_ledger(
                action="归属登记",
                target=entry,
                operator=operator,
                ok=True,
                message=f"成员 {entry['姓名']} 已登记入册，归属 {entry['岗位职务']}",
                before=None,
                after=STATUS_PENDING,
            )
            return dict(entry), []

    def run_action(
        self, entry_id: int, action: str, values: dict[str, Any] | None = None
    ) -> tuple[dict[str, Any] | None, str, bool]:
        """办理进场/离场/请假。

        返回 (成员, 说明, 是否已生效)；越权与不满足前置条件时成员原样返回、拒绝生效。
        多人同时办理同一成员时，由 crew_lock 串行化，离场只会生效一次。
        """
        values = values or {}
        with store.crew_lock:
            entry = store.find(MODULE, entry_id)
            if entry is None:
                return None, f"剧组成员 {entry_id} 不存在或已归档", False
            entry = self._normalize(entry)

            if action not in ACTION_RULES:
                self._append_ledger(action or "(空动作)", entry, self._resolve_operator(values),
                                    False, f"动作「{action}」不属于剧组人员可执行范围",
                                    entry["status"], entry["status"])
                return entry, f"动作「{action}」不属于剧组人员可执行范围", False

            operator = self._resolve_operator(values)
            if operator is None:
                message = "经办人不在剧组名册内，不得办理进场、离场或请假"
                self._append_ledger(action, entry, None, False, message,
                                    entry["status"], entry["status"])
                return entry, message, False

            allowed = _allowed_actions(str(operator.get("岗位职务", "")))
            if action not in allowed:
                rule = _role_of(str(operator.get("岗位职务", "")))
                if not allowed:
                    message = (
                        f"越权请求已拒绝：{operator.get('姓名')}（{operator.get('岗位职务')}）"
                        f"是只读成员，不能办理{action}"
                    )
                else:
                    role = rule["角色"] if rule else READONLY_ROLE
                    message = (
                        f"越权请求已拒绝：{role}仅可办理{'、'.join(_short_action(a) for a in allowed)}，"
                        f"不能办理{_short_action(action)}"
                    )
                self._append_ledger(action, entry, operator, False, message,
                                    entry["status"], entry["status"], denied=True)
                return entry, message, False

            rule = ACTION_RULES[action]
            before = entry["status"]
            if before != rule["allow_from"]:
                message = self._precondition_message(action, before)
                self._append_ledger(action, entry, operator, False, message, before, before)
                return entry, message, False

            entry["status"] = rule["target"]
            entry["在组状态"] = rule["target"]
            entry["pending"] = rule["target"] != STATUS_LEFT
            entry["abnormal"] = False
            if rule["date_field"] and not entry.get(rule["date_field"]):
                entry[rule["date_field"]] = _today()

            message = f"剧组成员 {entry['姓名']} 已{action}"
            self._append_ledger(action, entry, operator, True, message, before, rule["target"])
            return dict(entry), message, True

    # ---- 内部规则 ----

    def _normalize(self, entry: dict[str, Any]) -> dict[str, Any]:
        """把存量数据的在组状态对齐到统一状态字段，保证列表与统计同源。"""
        status = str(entry.get("status") or STATUS_PENDING)
        if status not in STATUS_ORDER:
            status = STATUS_PENDING
        entry["status"] = status
        entry["在组状态"] = status
        entry.setdefault("pending", status != STATUS_LEFT)
        entry.setdefault("abnormal", False)
        entry.setdefault("进场日期", "")
        entry.setdefault("离场日期", "")
        return entry

    def _resolve_operator(self, values: dict[str, Any]) -> dict[str, Any] | None:
        """经办人必须是名册内成员：先按编号、再按姓名匹配；岗位以后端名册为准。"""
        operator_id = values.get("经办人编号") or values.get("operatorId")
        name = values.get("经办人") or values.get("operator")
        with store.crew_lock:
            if operator_id is not None and str(operator_id).strip():
                text = str(operator_id).strip()
                for row in store.rows(MODULE):
                    if str(row.get("id")) == text or str(row.get("成员编号", "")).strip() == text:
                        return self._normalize(dict(row))
            if name and str(name).strip():
                text = str(name).strip()
                for row in store.rows(MODULE):
                    if str(row.get("姓名", "")).strip() == text:
                        return self._normalize(dict(row))
        return None

    def _operator_view(self, operator: dict[str, Any]) -> dict[str, Any]:
        position = str(operator.get("岗位职务", ""))
        rule = _role_of(position)
        return {
            "id": operator.get("id"),
            "成员编号": operator.get("成员编号"),
            "姓名": operator.get("姓名"),
            "岗位职务": position,
            "角色": rule["角色"] if rule else READONLY_ROLE,
            "可办动作": _allowed_actions(position),
            "只读": rule is None,
        }

    def _precondition_message(self, action: str, before: str) -> str:
        if action == ACTION_ENTER:
            if before == STATUS_ON_SET:
                return f"办理进场被拒绝：成员当前已在组，不得重复进场"
            if before == STATUS_LEFT:
                return "办理进场被拒绝：成员已离场，不能重新进场"
            if before == STATUS_ON_LEAVE:
                return "办理进场被拒绝：成员正在请假中，请先销假后再进场"
        if action == ACTION_LEAVE:
            if before == STATUS_PENDING:
                return "办理离场被拒绝：成员尚未进场，不能离场"
            if before == STATUS_LEFT:
                return "办理离场被拒绝：成员已离场，不得重复离场"
            if before == STATUS_ON_LEAVE:
                return "办理离场被拒绝：成员正在请假中，不能直接离场"
        if action == ACTION_LEAVE_REQUEST:
            if before == STATUS_PENDING:
                return "登记请假被拒绝：成员尚未进场，不能请假"
            if before == STATUS_LEFT:
                return "登记请假被拒绝：成员已离场，不能请假"
            if before == STATUS_ON_LEAVE:
                return "登记请假被拒绝：成员已在请假中，不得重复请假"
        return f"{action}被拒绝：当前状态「{before}」不允许办理"

    def _append_ledger(
        self,
        action: str,
        target: dict[str, Any] | None,
        operator: dict[str, Any] | None,
        ok: bool,
        message: str,
        before: str | None,
        after: str | None,
        denied: bool = False,
    ) -> None:
        """台账只追加；历史进出记录一经写入永不修改、永不删除。"""
        ledger = store.rows(LEDGER_MODULE)
        record = {
            "id": max((int(row.get("id", 0)) for row in ledger), default=0) + 1,
            "时间": _now(),
            "动作": action,
            "成员编号": target.get("成员编号") if target else "",
            "成员姓名": target.get("姓名") if target else "",
            "经办人编号": operator.get("成员编号") if operator else "",
            "经办人姓名": operator.get("姓名") if operator else "",
            "经办人岗位": operator.get("岗位职务") if operator else "非名册人员",
            "变更前状态": before or "",
            "变更后状态": after or "",
            "结果": "已通过" if ok else "已拒绝",
            "越权": denied,
            "说明": message,
        }
        ledger.append(record)

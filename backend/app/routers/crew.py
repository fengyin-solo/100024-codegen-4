"""剧组人员接口：维护剧组成员，并按岗位职务限制进场、离场、请假的办理权限。

人员列表、在组统计、权限台账共享同一份成员数据；路由层不做业务判断，
所有权限与状态规则都在 CrewService 里收口。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.crew import ACTIONS, CrewService

router = APIRouter(prefix="/api/crew", tags=["剧组人员"])

service = CrewService()

LIST_FIELDS = ["成员编号", "姓名", "岗位职务", "所属组别", "联系电话", "进场日期", "离场日期", "在组状态"]
STATUSES = ["待进场", "在组", "已离场", "已请假"]
LEDGER_COLUMNS = ["时间", "动作", "成员编号", "成员姓名", "经办人岗位", "经办人姓名", "变更前状态", "变更后状态", "结果", "说明"]


def _page_size(size: int) -> None:
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")


@router.get("/stats", response_model=dict)
def crew_stats() -> dict[str, Any]:
    """在组统计：与人员列表同源，成员归属变化后同步变化。"""
    return service.stats()


@router.get("/permissions", response_model=dict)
def crew_permissions(
    operator_id: str | None = Query(default=None, description="经办人成员编号或内部 id"),
) -> dict[str, Any]:
    """岗位职务权限矩阵：制片、场务、演员统筹各能办理哪些动作，其余岗位只读。"""
    return service.permissions(operator_id)


@router.get("/ledger", response_model=PageResult[dict])
def crew_ledger(
    keyword: str | None = Query(default=None, description="按成员编号、姓名或经办人检索"),
    action: str | None = Query(default=None, description=f"动作：{'、'.join(ACTIONS)}、归属登记"),
    result: str | None = Query(default=None, description="已通过 / 已拒绝"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """权限归属台账：只追加、不可改，记录每次办理与拒绝。"""
    _page_size(size)
    items, total = service.list_ledger(keyword=keyword, action=action, result=result, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按成员编号或姓名检索"),
    status: str | None = Query(default=None, description="待进场、在组、已离场、已请假"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按成员编号、姓名与状态过滤剧组人员列表；没有数据时返回空页，不报错。"""
    _page_size(size)
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出剧组人员清单：成员、在组统计与台账一起给出，保证三方口径一致。"""
    items, total = service.list_entries(page=1, size=10000)
    ledger, ledger_total = service.list_ledger(page=1, size=10000)
    return {
        "module": "crew",
        "total": total,
        "items": items,
        "stats": service.stats(),
        "ledger_total": ledger_total,
        "ledger": ledger,
    }


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条剧组成员明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"剧组成员 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条剧组成员，缺字段时说明原因而不是静默丢弃。"""
    entry, errors = service.create_entry(payload.values)
    if entry is None:
        # 字段缺失以“缺少必填字段”提示；其余（越权、非名册）原样回传拒绝原因
        if errors and service.missing_fields(payload.values) == errors:
            return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(errors)}")
        return ActionResult(ok=False, message="；".join(errors))
    return ActionResult(ok=True, message="剧组成员已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """办理进场、离场、请假：经办人岗位越权、状态不允许或重复离场都会被拒绝。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message, applied = service.run_action(entry_id, action, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=applied, message=message, entry=entry)

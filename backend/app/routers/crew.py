"""剧组人员接口：维护剧组成员，覆盖办理进场、办理离场、登记请假与归属调整。

经办人身份通过请求头 ``X-Operator-Id``（剧组成员 id）或请求体里的
``operatorId`` 指定；服务层按经办人岗位职务判定权限，越权请求一律 403。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import ValidationError

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.crew import (
    ACTIONS,
    ConflictError,
    PermissionError as CrewPermissionError,
    CrewService,
)

router = APIRouter(prefix="/api/crew", tags=["剧组人员"])

service = CrewService()

LIST_FIELDS = ["成员编号", "姓名", "岗位职务", "所属组别", "联系电话", "进场日期", "离场日期", "在组状态"]
STATUSES = ["待进场", "在组", "已离场", "已请假"]


async def _parse_payload(request: Request) -> EntryPayload:
    """兼容两种请求体：{"values": {...}} 包装写法与 {action, operatorId} 平铺写法。"""
    raw = await request.json()
    if not isinstance(raw, dict):
        raise HTTPException(status_code=400, detail="请求体必须是 JSON 对象")
    if isinstance(raw.get("values"), dict):
        try:
            return EntryPayload(**raw)
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=exc.errors())
    values = {k: v for k, v in raw.items() if k != "remark"}
    return EntryPayload(values=values, remark=raw.get("remark"))


def _resolve_operator_id(payload: EntryPayload, request: Request) -> int | None:
    """优先取请求头 X-Operator-Id，其次取请求体 operatorId。"""
    raw = request.headers.get("X-Operator-Id")
    if raw is None or not str(raw).strip():
        raw = payload.values.get("operatorId") if payload and payload.values else None
    if raw is None or not str(raw).strip():
        return None
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="经办人成员编号必须是数字")


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按成员编号或姓名检索"),
    status: str | None = Query(default=None, description="待进场、在组、已离场、已请假"),
    group: str | None = Query(default=None, description="按所属组别筛选"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按成员编号、姓名、状态与组别过滤剧组人员列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, group=group, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/ledger", response_model=dict)
def crew_ledger() -> dict[str, Any]:
    """权限归属台账：权限矩阵、在组统计、成员权限、进出流水，同一份成员数据实时汇总。"""
    return service.ledger()


@router.get("/stats", response_model=dict)
def crew_stats() -> dict[str, Any]:
    """在组统计：与人员列表、权限台账共享同一份成员数据。"""
    return service.stats()


@router.get("/history", response_model=PageResult[dict])
def crew_history(
    keyword: str | None = Query(default=None, description="按成员编号或姓名检索"),
    action: str | None = Query(default=None, description=f"{'、'.join(ACTIONS)}、归属变更"),
    page: int = 1,
    size: int = 50,
) -> PageResult[dict]:
    """进出/请假/归属变更流水：只追加不改写，历史记录保持原样。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.history(keyword=keyword, action=action, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条剧组成员明细；不存在时给出可读的错误说明。只读成员也可查看。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"剧组成员 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条剧组成员，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="剧组成员已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
async def run_action(entry_id: int, request: Request) -> ActionResult:
    """对单条剧组成员执行办理进场、办理离场、登记请假。

    岗位职务限定制片、场务、演员统筹的可办动作；越权与只读成员的改动一律拒绝，
    同一成员并发办理离场时只允许一个成功，其余按重复离场拒绝。
    """
    payload = await _parse_payload(request)
    action = str(payload.values.get("action") or "").strip()
    operator_id = _resolve_operator_id(payload, request)
    try:
        entry, message = service.run_action(entry_id, action, operator_id=operator_id, remark=payload.remark)
    except CrewPermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/{entry_id}/reassign", response_model=ActionResult)
async def reassign_entry(entry_id: int, request: Request) -> ActionResult:
    """调整成员岗位职务/所属组别归属；仅制片可办理，列表、统计、台账共享同一结果。"""
    payload = await _parse_payload(request)
    operator_id = _resolve_operator_id(payload, request)
    try:
        entry, message = service.reassign(entry_id, payload.values, operator_id=operator_id)
    except CrewPermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.get("/export/all")
def export_entries() -> dict[str, Any]:
    """导出剧组人员清单与台账：返回当前全量成员、统计与历史进出记录。"""
    items, total = service.list_entries(page=1, size=10000)
    history_items, history_total = service.history(page=1, size=10000)
    return {
        "module": "crew",
        "total": total,
        "items": items,
        "stats": service.stats(),
        "history_total": history_total,
        "history": history_items,
    }

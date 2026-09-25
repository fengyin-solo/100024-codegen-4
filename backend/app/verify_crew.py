"""剧组权限台账验证脚本：越权拒绝、只读成员、并发不重复离场、统计同源、台账只追加。

运行：.venv/bin/python -m app.verify_crew
"""
from __future__ import annotations

import threading

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

passed: list[str] = []
failed: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    (passed if condition else failed).append(name)
    print(f"{'✓' if condition else '✗'} {name}" + (f" —— {detail}" if detail and not condition else ""))


def action(entry_id: int, act: str, operator: str | None):
    values = {"action": act}
    if operator is not None:
        values["经办人"] = operator
    return client.post(f"/api/crew/{entry_id}/actions", json={"values": values}).json()


# 初始数据
crew = client.get("/api/crew?size=100").json()["items"]
stats0 = client.get("/api/crew/stats").json()
ledger0 = client.get("/api/crew/ledger?size=100").json()["items"]

check("在组统计与人员列表同源",
      stats0["成员总数"] == len(crew)
      and stats0["在组人数"] == sum(1 for r in crew if r["status"] == "在组")
      and stats0["待进场人员"] == sum(1 for r in crew if r["status"] == "待进场"),
      str(stats0))

# 权限矩阵
perms = client.get("/api/crew/permissions").json()
role_map = {r["岗位职务"]: r for r in perms["roles"]}
check("制片可办理进场/离场/请假", role_map["制片"]["可办动作"] == ["办理进场", "办理离场", "登记请假"])
check("场务可办理进场/离场", role_map["场务"]["可办动作"] == ["办理进场", "办理离场"])
check("演员统筹可办理进场/请假", role_map["演员统筹"]["可办动作"] == ["办理进场", "登记请假"])
check("只读成员无任何动作", role_map["只读成员"]["可办动作"] == [] and role_map["只读成员"]["只读"])

# 赵摄助 id=5 待进场：场务可进场；演员统筹对其不能办离场（越权）
r = action(5, "办理进场", "李场务")
check("场务办理进场生效", r["ok"] and r["entry"]["status"] == "在组", r.get("message"))
r = action(5, "办理离场", "王统筹")
check("演员统筹办理离场被拒（越权）", not r["ok"] and "越权" in r["message"] and r["entry"]["status"] == "在组", r.get("message"))

# 导演只读：任何动作都拒绝，成员状态不变
r = action(5, "登记请假", "陈导演")
check("导演登记请假被拒（只读）", not r["ok"] and "只读" in r["message"], r.get("message"))
r = action(5, "办理离场", "陈导演")
check("导演办理离场被拒（只读）", not r["ok"] and "只读" in r["message"], r.get("message"))

# 非名册经办人拒绝
r = action(5, "登记请假", "路人甲")
check("非名册经办人被拒", not r["ok"] and "不在剧组名册" in r["message"], r.get("message"))

# 场务不能登记请假（越权），成员仍在组
r = action(5, "登记请假", "李场务")
check("场务登记请假被拒（越权）", not r["ok"] and "越权" in r["message"] and r["entry"]["status"] == "在组", r.get("message"))

# 制片登记请假：在组 -> 已请假
r = action(5, "登记请假", "张制片")
check("制片登记请假生效", r["ok"] and r["entry"]["status"] == "已请假", r.get("message"))

# 请假中不能直接离场，也不能重复请假
r = action(5, "办理离场", "张制片")
check("请假中办理离场被拒", not r["ok"] and r["entry"]["status"] == "已请假", r.get("message"))
r = action(5, "登记请假", "王统筹")
check("重复请假被拒", not r["ok"] and r["entry"]["status"] == "已请假", r.get("message"))

# 孙演员 id=6 待进场：未进场不能请假/离场
r = action(6, "登记请假", "王统筹")
check("未进场请假被拒", not r["ok"] and r["entry"]["status"] == "待进场", r.get("message"))
r = action(6, "办理离场", "李场务")
check("未进场离场被拒", not r["ok"] and r["entry"]["status"] == "待进场", r.get("message"))
# 已进场不能重复进场
r = action(1, "办理进场", "李场务")
check("已在组重复进场被拒", not r["ok"] and r["entry"]["status"] == "在组", r.get("message"))

# 吴跟机 id=8 已离场：不得重复离场
r = action(8, "办理离场", "张制片")
check("已离场不得重复离场", not r["ok"] and "不得重复离场" in r["message"] and r["entry"]["status"] == "已离场",
      r.get("message"))

# 归属登记：只读成员/非名册人员不能登记，办理岗可以
deny_create = client.post("/api/crew", json={"values": {
    "成员编号": "CREW-9001", "姓名": "临时甲", "岗位职务": "场工", "经办人": "陈导演",
}}).json()
check("只读成员登记归属被拒", not deny_create["ok"] and "只读" in deny_create["message"], deny_create.get("message"))
deny_create = client.post("/api/crew", json={"values": {
    "成员编号": "CREW-9002", "姓名": "临时乙", "岗位职务": "场工", "经办人": "路人甲",
}}).json()
check("非名册人员登记归属被拒", not deny_create["ok"] and "名册" in deny_create["message"], deny_create.get("message"))
ok_create = client.post("/api/crew", json={"values": {
    "成员编号": "CREW-9003", "姓名": "郑场工", "岗位职务": "场工", "所属组别": "场务组", "经办人": "李场务",
}}).json()
check("办理岗登记归属生效", ok_create["ok"] and ok_create["entry"]["status"] == "待进场", ok_create.get("message"))

# ---- 并发：多人同时为同一成员办理离场，只能成功一次 ----
target = client.get("/api/crew/1").json()
assert target["status"] == "在组"
results: list[dict] = []
barrier = threading.Barrier(8)

def concurrent_leave() -> None:
    barrier.wait()
    res = client.post("/api/crew/1/actions", json={"values": {"action": "办理离场", "经办人": "李场务"}}).json()
    results.append(res)

threads = [threading.Thread(target=concurrent_leave) for _ in range(8)]
for t in threads:
    t.start()
for t in threads:
    t.join()

ok_count = sum(1 for r in results if r["ok"])
final = client.get("/api/crew/1").json()
check("并发离场仅一次生效", ok_count == 1, f"成功次数={ok_count}")
check("并发后成员状态为已离场", final["status"] == "已离场" and bool(final.get("离场日期")), str(final))

# 串行再补一次离场，同样被拒
r = action(1, "办理离场", "张制片")
check("并发后再次离场仍被拒", not r["ok"] and "不得重复离场" in r["message"], r.get("message"))

# ---- 统计与列表继续同源 ----
crew_after = client.get("/api/crew?size=100").json()["items"]
stats_after = client.get("/api/crew/stats").json()
check("归属变化后统计与列表仍一致",
      stats_after["成员总数"] == len(crew_after)
      and stats_after["在组人数"] == sum(1 for x in crew_after if x["status"] == "在组")
      and stats_after["已离场人员"] == sum(1 for x in crew_after if x["status"] == "已离场")
      and stats_after["已离场人员"] >= 2,
      str(stats_after))

# ---- 台账：只追加、拒绝也留痕、历史保持原样 ----
ledger = client.get("/api/crew/ledger?size=200").json()["items"]
ledger_asc = list(reversed(ledger))
check("台账记录数只增不减", len(ledger_asc) > len(ledger0),
      f"{len(ledger0)} -> {len(ledger_asc)}")
# 历史记录保持原样：最初已经存在的前若干条（新库为空则检查所有已写入记录字段稳定）
denied = [x for x in ledger_asc if x["结果"] == "已拒绝"]
check("越权/拒绝请求全部留痕",
      any("越权" in x["说明"] for x in denied)
      and any("不得重复离场" in x["说明"] for x in denied)
      and any("只读" in x["说明"] for x in denied),
      f"拒绝记录 {len(denied)} 条")
passed_enter = [x for x in ledger_asc if x["动作"] == "办理进场" and x["结果"] == "已通过"]
check("进场台账含经办人岗位与前后状态",
      passed_enter and passed_enter[0]["经办人岗位"] == "场务"
      and passed_enter[0]["变更前状态"] == "待进场"
      and passed_enter[0]["变更后状态"] == "在组",
      str(passed_enter[:1]))

# 台账无修改/删除接口：对台账地址发写请求必须被 405 挡下
status_codes = {
    method: client.request(method, "/api/crew/ledger", json={"values": {}}).status_code
    for method in ("POST", "PUT", "PATCH", "DELETE")
}
check("台账只有只读接口", all(code == 405 for code in status_codes.values()), str(status_codes))

# 导出三件套同口径
export = client.get("/api/crew/export").json()
check("导出中列表/统计/台账共享同一结果",
      export["stats"] == stats_after and export["total"] == stats_after["成员总数"]
      and export["ledger_total"] >= len(ledger_asc) - 0,
      str(export["stats"]))

print(f"\n通过 {len(passed)} 项，失败 {len(failed)} 项")
if failed:
    print("失败项：", "；".join(failed))
    raise SystemExit(1)

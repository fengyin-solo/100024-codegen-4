"""剧组成员权限归属台账的验收测试。

覆盖：
- 岗位职务限定制片/场务/演员统筹的进场、离场、请假动作；越权与只读一律拒绝
- 归属变化后人员列表、在组统计、台账看到同一结果
- 多人并发办理同一成员离场不重复
- 历史进出记录只追加不改写
"""
from __future__ import annotations

import concurrent.futures

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client() -> TestClient:
    # 每个用例拿一份全新的内存仓库，避免状态互相串；
    # 业务服务用的是 `from app.store import store` 绑定，需要同步替换。
    import app.store as store_module
    from app.services import crew as crew_service

    fresh = store_module.Store()
    store_module.store = fresh
    crew_service.store = fresh
    return TestClient(app)


PRODUCER = 1   # 张制片 / 制片人 / 在组
STAGEHAND = 2  # 李场务 / 场务组长 / 待进场
COORDINATOR = 3  # 王统筹 / 演员统筹 / 在组
READONLY = 4   # 赵摄影 / 摄影师 / 在组


def act(client: TestClient, member_id: int, action: str, operator: int | None):
    headers = {"X-Operator-Id": str(operator)} if operator is not None else {}
    return client.post(
        f"/api/crew/{member_id}/actions",
        json={"action": action},
        headers=headers,
    )


# ---------- 岗位权限矩阵 ----------

def test_producer_can_do_all_three_actions(client: TestClient) -> None:
    # 制片给待进场场务办进场
    assert act(client, STAGEHAND, "办理进场", PRODUCER).status_code == 200
    # 制片给在组的统筹办请假
    assert act(client, COORDINATOR, "登记请假", PRODUCER).status_code == 200
    # 制片给已请假的统筹办离场
    response = act(client, COORDINATOR, "办理离场", PRODUCER)
    assert response.status_code == 200
    assert response.json()["entry"]["status"] == "已离场"


def test_stagehand_can_only_check_in(client: TestClient) -> None:
    assert act(client, STAGEHAND, "办理进场", STAGEHAND).status_code == 200
    denied = act(client, READONLY, "办理离场", STAGEHAND)
    assert denied.status_code == 403
    assert "越权" in denied.json()["detail"]
    denied_leave = act(client, READONLY, "登记请假", STAGEHAND)
    assert denied_leave.status_code == 403


def test_casting_coordinator_can_only_leave(client: TestClient) -> None:
    assert act(client, READONLY, "登记请假", COORDINATOR).status_code == 200
    denied_in = act(client, STAGEHAND, "办理进场", COORDINATOR)
    assert denied_in.status_code == 403
    assert "越权" in denied_in.json()["detail"]
    denied_out = act(client, READONLY, "办理离场", COORDINATOR)
    assert denied_out.status_code == 403


def test_readonly_member_cannot_change_anything(client: TestClient) -> None:
    for action in ("办理进场", "办理离场", "登记请假"):
        response = act(client, PRODUCER, action, READONLY)
        assert response.status_code == 403
        assert "只读成员" in response.json()["detail"]


def test_request_without_operator_is_rejected(client: TestClient) -> None:
    response = act(client, READONLY, "办理离场", None)
    assert response.status_code == 403
    assert "经办人" in response.json()["detail"]


def test_unknown_action_rejected(client: TestClient) -> None:
    response = act(client, READONLY, "删除成员", PRODUCER)
    assert response.json()["ok"] is False


# ---------- 状态流转：不得重复离场 ----------

def test_duplicate_checkout_conflict(client: TestClient) -> None:
    assert act(client, READONLY, "办理离场", PRODUCER).status_code == 200
    again = act(client, READONLY, "办理离场", PRODUCER)
    assert again.status_code == 409
    assert "重复离场" in again.json()["detail"]


def test_leave_requires_on_set(client: TestClient) -> None:
    # 待进场不能请假、不能离场
    assert act(client, STAGEHAND, "登记请假", PRODUCER).status_code == 409
    assert act(client, STAGEHAND, "办理离场", PRODUCER).status_code == 409


def test_concurrent_checkout_only_one_succeeds(client: TestClient) -> None:
    def fire(_: int) -> int:
        return act(client, READONLY, "办理离场", PRODUCER).status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        statuses = list(pool.map(fire, range(16)))

    assert statuses.count(200) == 1
    assert statuses.count(409) == 15
    detail = client.get(f"/api/crew/{READONLY}").json()
    assert detail["status"] == "已离场"


# ---------- 归属变化：列表、统计、台账共享同一结果 ----------

def test_reassign_only_producer_and_reflects_everywhere(client: TestClient) -> None:
    # 演员统筹无权调归属
    denied = client.post(
        f"/api/crew/{STAGEHAND}/reassign",
        json={"岗位职务": "制片人"},
        headers={"X-Operator-Id": str(COORDINATOR)},
    )
    assert denied.status_code == 403

    # 制片把场务组长调成演员统筹
    response = client.post(
        f"/api/crew/{STAGEHAND}/reassign",
        json={"岗位职务": "演员统筹", "所属组别": "演员组"},
        headers={"X-Operator-Id": str(PRODUCER)},
    )
    assert response.status_code == 200

    member = client.get(f"/api/crew/{STAGEHAND}").json()
    stats = client.get("/api/crew/stats").json()
    ledger = client.get("/api/crew/ledger").json()
    ledger_row = next(item for item in ledger["成员台账"] if item["id"] == STAGEHAND)
    list_row = client.get(f"/api/crew?keyword=CREW-0002").json()["items"][0]

    # 三处看到的归属完全一致
    assert member["岗位职务"] == "演员统筹"
    assert list_row["岗位职务"] == "演员统筹"
    assert ledger_row["岗位职务"] == "演员统筹"
    assert ledger_row["所属组别"] == "演员组"
    # 台账与独立统计接口数值一致
    assert ledger["在组统计"] == stats
    # 权限随新岗位变化：只能请假，进场被拒
    assert ledger_row["可办理动作"] == ["登记请假"]
    assert act(client, STAGEHAND, "办理进场", STAGEHAND).status_code == 403
    assert act(client, READONLY, "登记请假", STAGEHAND).status_code == 200


# ---------- 历史进出记录保持原样 ----------

def test_history_is_append_only_and_preserved(client: TestClient) -> None:
    before = client.get("/api/crew/history?size=100").json()
    seeded = before["items"]
    assert before["total"] >= 2
    # 种子记录原样保留
    assert any(item["成员编号"] == "CREW-0005" and item["动作类型"] == "办理进场" for item in seeded)

    act(client, READONLY, "登记请假", COORDINATOR)
    act(client, READONLY, "办理离场", PRODUCER)

    after = client.get("/api/crew/history?size=100").json()
    assert after["total"] == before["total"] + 2

    by_id = {item["id"]: item for item in after["items"]}
    # 旧记录字段未被后续动作改写
    old_checkin = next(item for item in seeded if item["id"] == 1)
    assert by_id[1] == old_checkin
    assert by_id[1]["变更后状态"] == "在组"
    # 新记录按动作追加，且记录了办理人与岗位
    new_actions = [item["动作类型"] for item in after["items"] if item["id"] > before["total"]]
    assert "登记请假" in new_actions and "办理离场" in new_actions


def test_ledger_exposes_permission_matrix(client: TestClient) -> None:
    matrix = client.get("/api/crew/ledger").json()["权限矩阵"]
    assert set(matrix) == {"制片", "场务", "演员统筹", "只读成员"}
    assert set(matrix["制片"]["可办理动作"]) == {"办理进场", "办理离场", "登记请假"}
    assert matrix["场务"]["可办理动作"] == ["办理进场"]
    assert matrix["演员统筹"]["可办理动作"] == ["登记请假"]
    assert matrix["只读成员"]["可办理动作"] == []

"""SSE 建流即重放 pending interrupt + pub/sub fan-out 的回归测试。

背景一（interrupt 补发）：configure_chapter_grouping 紧接 run 启动即 interrupt（无 LLM 前置），
实时 interrupt 事件可能在客户端建流窗口内落空，导致右侧交互区永久卡空态。
修复：/stream 建流时补发一次当前 pending interrupt（见 endpoints/runs.py）。

背景二（fan-out）：旧实现每 run 一个共享单消费者队列，同 run 重连窗口内新旧
generator 抢 q.get()，事件被已断开的旧连接"偷"走（interrupt/run_complete 静默丢失）。
修复：每连接私有队列，push_event 扇出（见 graph_runner.subscribe_sse/push_event）。

种子方式：httpx ASGITransport 在 client.get() 内跑完整个响应，且私有队列在订阅前
不存在——事件必须由后台 pusher 在订阅出现后经 push_event 扇出（端点阻塞在
q.get() 时让出事件循环给 pusher）。
"""

import asyncio
from unittest.mock import AsyncMock

_IDLE_STATE = {
    "status": "running",
    "node_statuses": {},
    "delegated_scope": None,
    "active_interaction": None,
}


def _data_events(body: str) -> list[dict]:
    import json

    out = []
    for line in body.splitlines():
        if line.startswith("data: "):
            out.append(json.loads(line[len("data: ") :]))
    return out


def _seed_when_subscribed(gr, run_id: str, events: list[dict], *, subscribers: int = 1):
    """等 /stream 完成订阅（数量达标）后再推事件，返回 pusher task 供 await 收尾。"""

    async def _pusher():
        for _ in range(1000):
            if len(gr._sse_subscribers.get(run_id, ())) >= subscribers:
                break
            await asyncio.sleep(0.005)
        for ev in events:
            await gr.push_event(run_id, ev)

    return asyncio.create_task(_pusher())


async def test_stream_replays_pending_interrupt_on_connect(client, mock_runner, monkeypatch):
    """建流时若 run 处于 waiting_human，应先补发一条 interrupt 事件（含 node + payload），
    再进入正常事件流。这正是 configure_chapter_grouping 早中断丢事件的兜底。"""
    run_id = "run-grouping-1"
    monkeypatch.setattr(mock_runner, "get_current_run_state", AsyncMock(
        return_value={
            "status": "waiting_human",
            "node_statuses": {"main/configure_chapter_grouping": "waiting_human"},
            "delegated_scope": None,
            "active_interaction": {
                "scope": "main",
                "thread_id": run_id,
                "node": "configure_chapter_grouping",
                "path": "main/configure_chapter_grouping",
                "payload": {
                    "type": "chapter_grouping",
                    "chapter_count": 10,
                    "default_group_size": 1,
                    "max_group_size": 5,
                },
            },
        }
    ))
    # run_complete 让 event_generator 在补发后正常收尾，避免挂在 30s 心跳超时。
    task = _seed_when_subscribed(mock_runner, run_id, [
        {"type": "run_complete", "scope": "main", "thread_id": run_id},
    ])

    resp = await client.get(f"/runs/{run_id}/stream")
    await task
    assert resp.status_code == 200
    events = _data_events(resp.text)

    # 第一条必须是补发的 interrupt，且带前端建面板所需的 node + payload
    assert events[0]["type"] == "interrupt"
    assert events[0]["status"] == "waiting_human"
    assert events[0]["node"] == "configure_chapter_grouping"
    assert events[0]["node_path"] == "main/configure_chapter_grouping"
    assert events[0]["scope"] == "main"
    assert events[0]["thread_id"] == run_id
    assert events[0]["payload"]["type"] == "chapter_grouping"
    assert events[0]["payload"]["chapter_count"] == 10
    # 随后仍照常转发队列事件
    assert events[-1]["type"] == "run_complete"


async def test_stream_no_replay_when_no_pending_interrupt(client, mock_runner, monkeypatch):
    """无 pending interrupt（如已 resume / 运行中）时不得补发 interrupt，避免误开面板。"""
    run_id = "run-grouping-2"
    monkeypatch.setattr(mock_runner, "get_current_run_state", AsyncMock(
        return_value={**_IDLE_STATE, "node_statuses": {"main/load_config": "done"}}
    ))
    task = _seed_when_subscribed(mock_runner, run_id, [
        {"type": "run_complete", "scope": "main", "thread_id": run_id},
    ])

    resp = await client.get(f"/runs/{run_id}/stream")
    await task
    assert resp.status_code == 200
    events = _data_events(resp.text)

    assert all(e["type"] != "interrupt" for e in events)
    assert events[0]["type"] == "run_complete"


async def test_stream_survives_resolution_failure(client, mock_runner, monkeypatch):
    """补发是尽力而为：get_current_run_state 抛错不得中断正常事件流。"""
    run_id = "run-grouping-3"
    monkeypatch.setattr(mock_runner, "get_current_run_state", AsyncMock(side_effect=RuntimeError("boom")))
    task = _seed_when_subscribed(mock_runner, run_id, [
        {"type": "run_complete", "scope": "main", "thread_id": run_id},
    ])

    resp = await client.get(f"/runs/{run_id}/stream")
    await task
    assert resp.status_code == 200
    events = _data_events(resp.text)
    assert events[0]["type"] == "run_complete"


async def test_two_streams_both_receive_events(client, mock_runner, monkeypatch):
    """fan-out 核心回归：同 run 两条并发流（双 tab / 重连窗口）都收到全部事件，
    不再被单消费者队列分流。"""
    run_id = "run-fanout-1"
    monkeypatch.setattr(mock_runner, "get_current_run_state", AsyncMock(return_value=_IDLE_STATE))
    task = _seed_when_subscribed(mock_runner, run_id, [
        {"type": "node_status", "node_path": "main/load_config", "status": "done"},
        {"type": "run_complete", "scope": "main", "thread_id": run_id},
    ], subscribers=2)

    r1, r2 = await asyncio.gather(
        client.get(f"/runs/{run_id}/stream"),
        client.get(f"/runs/{run_id}/stream"),
    )
    await task
    for resp in (r1, r2):
        assert resp.status_code == 200
        assert [e["type"] for e in _data_events(resp.text)] == ["node_status", "run_complete"]


async def test_subscriber_unregistered_after_run_complete(client, mock_runner, monkeypatch):
    """流正常收尾后订阅注销、registry 清空（无内存残留）。"""
    run_id = "run-fanout-2"
    monkeypatch.setattr(mock_runner, "get_current_run_state", AsyncMock(return_value=_IDLE_STATE))
    task = _seed_when_subscribed(mock_runner, run_id, [{"type": "run_complete"}])

    resp = await client.get(f"/runs/{run_id}/stream")
    await task
    assert resp.status_code == 200
    assert run_id not in mock_runner._sse_subscribers


async def test_run_deleted_sentinel_closes_stream(client, mock_runner, monkeypatch):
    """delete_run 发出的 run_deleted 哨兵须终止打开的流（如另一 tab），防永久心跳。"""
    run_id = "run-del-1"
    monkeypatch.setattr(mock_runner, "get_current_run_state", AsyncMock(return_value=_IDLE_STATE))
    task = _seed_when_subscribed(mock_runner, run_id, [{"type": "run_deleted", "run_id": run_id}])

    resp = await client.get(f"/runs/{run_id}/stream")
    await task
    assert resp.status_code == 200
    events = _data_events(resp.text)
    assert events[-1]["type"] == "run_deleted"
    assert run_id not in mock_runner._sse_subscribers


async def test_stream_404_for_unknown_run(client, mock_runner):
    """未知 run 建流应 404，而非挂着心跳空转。"""
    mock_runner._runs_db.get = AsyncMock(return_value=None)
    resp = await client.get("/runs/ghost/stream")
    assert resp.status_code == 404

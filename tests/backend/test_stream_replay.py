"""SSE 建流即重放 pending interrupt 的回归测试。

背景：configure_chapter_grouping 紧接 run 启动即 interrupt（无 LLM 前置），
实时 interrupt 事件可能在客户端建流窗口内落空，导致右侧交互区永久卡空态。
修复：/stream 建流时补发一次当前 pending interrupt（见 endpoints/runs.py）。
"""

from unittest.mock import AsyncMock


def _data_events(body: str) -> list[dict]:
    import json

    out = []
    for line in body.splitlines():
        if line.startswith("data: "):
            out.append(json.loads(line[len("data: ") :]))
    return out


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
    # 预置 run_complete 让 event_generator 在补发后正常收尾，避免挂在 30s 心跳超时。
    q = mock_runner.get_or_create_sse_queue(run_id)
    q.put_nowait({"type": "run_complete", "scope": "main", "thread_id": run_id})

    resp = await client.get(f"/runs/{run_id}/stream")
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
        return_value={
            "status": "running",
            "node_statuses": {"main/load_config": "done"},
            "delegated_scope": None,
            "active_interaction": None,
        }
    ))
    q = mock_runner.get_or_create_sse_queue(run_id)
    q.put_nowait({"type": "run_complete", "scope": "main", "thread_id": run_id})

    resp = await client.get(f"/runs/{run_id}/stream")
    assert resp.status_code == 200
    events = _data_events(resp.text)

    assert all(e["type"] != "interrupt" for e in events)
    assert events[0]["type"] == "run_complete"


async def test_stream_survives_resolution_failure(client, mock_runner, monkeypatch):
    """补发是尽力而为：get_current_run_state 抛错不得中断正常事件流。"""
    run_id = "run-grouping-3"
    monkeypatch.setattr(mock_runner, "get_current_run_state", AsyncMock(side_effect=RuntimeError("boom")))
    q = mock_runner.get_or_create_sse_queue(run_id)
    q.put_nowait({"type": "run_complete", "scope": "main", "thread_id": run_id})

    resp = await client.get(f"/runs/{run_id}/stream")
    assert resp.status_code == 200
    events = _data_events(resp.text)
    assert events[0]["type"] == "run_complete"

"""render_service 服务层单测（不连图/DB，纯桩 runner 状态读取）。"""

from unittest.mock import AsyncMock

import services.render_service as render_service


async def test_get_render_chapters_attaches_group_label(monkeypatch):
    """分组单元返回人读 label「第A-B章」；成员由 chapter_groups[ch_id] 推算。"""
    monkeypatch.setattr(
        render_service, "_get_novel_dir", AsyncMock(return_value="/novel")
    )
    monkeypatch.setattr(
        render_service,
        "_get_shared_state",
        AsyncMock(
            return_value={
                "chapters_status": {"ch0001-0003": "pending"},
                "render_batch": [],
                "chapter_groups": {
                    "ch0001-0003": [
                        "chapter_001_a",
                        "chapter_002_b",
                        "chapter_003_c",
                    ]
                },
            }
        ),
    )

    chapters = await render_service.get_render_chapters("run-1")

    assert len(chapters) == 1
    assert chapters[0]["chapter_id"] == "ch0001-0003"
    assert chapters[0]["label"] == "第1-3章"


async def test_get_render_chapters_label_empty_when_group_missing(monkeypatch):
    """组信息缺失时 label 置空字符串（不抛错、不做 I/O）。"""
    monkeypatch.setattr(
        render_service, "_get_novel_dir", AsyncMock(return_value="/novel")
    )
    monkeypatch.setattr(
        render_service,
        "_get_shared_state",
        AsyncMock(
            return_value={
                "chapters_status": {"ch0001": "pending"},
                "render_batch": [],
                "chapter_groups": {},
            }
        ),
    )

    chapters = await render_service.get_render_chapters("run-1")

    assert chapters[0]["label"] == ""

"""RenderSession 单测：聚焦不触发真实 GPU worker 的纯逻辑分支。"""

from novel2media import render_state


def _make_session(tmp_path, monkeypatch):
    """构造一个不会启动真实 worker、不连 ComfyUI 的 RenderSession。"""
    import services.render_session as rs

    # 桩掉 worker 启动（避免 create_task 需要事件循环 + 真实 GPU 调用）
    monkeypatch.setattr(rs.RenderSession, "_ensure_worker", lambda self: None)
    # 桩掉配置加载与客户端构建（不依赖 services.json / 不连服务器）
    monkeypatch.setattr(rs, "_load_services_config", lambda novel_dir: _FakeCfg())
    monkeypatch.setattr(rs, "ComfyUIClient", lambda *a, **k: object())

    novel_dir = str(tmp_path / "novel")
    return rs.RenderSession("run-1", novel_dir, "ch1", push_event=None), novel_dir


class _FakeCfg:
    comfyui_url = "http://fake:8188"
    comfyui_timeout = 120


def test_enqueue_reroll_persists_changed_prompt(tmp_path, monkeypatch):
    """改词 reroll → 新 prompt 回写 render_state（节点重入用改后的 prompt 算指纹）。"""
    session, novel_dir = _make_session(tmp_path, monkeypatch)
    render_state.save(
        novel_dir,
        "ch1",
        {
            "chapter_id": "ch1",
            "shots": {
                "0": {
                    "storyboard_id": 0, "workflow": "qwen_t2i", "prompt": "old",
                    "ref_images": [], "subjects": [],
                    "candidates": ["/a.png"], "selected": "/a.png",
                    "status": "done", "error": None,
                }
            },
        },
    )

    session.enqueue_reroll(0, prompt="new prompt")

    data = render_state.load(novel_dir, "ch1")
    assert data["shots"]["0"]["prompt"] == "new prompt"
    # job 已入队，prompt 为改后的
    assert session._queue[-1]["prompt"] == "new prompt"


def test_enqueue_reroll_keeps_old_prompt_when_none(tmp_path, monkeypatch):
    """prompt 为 None → 沿用旧 prompt，不改 render_state。"""
    session, novel_dir = _make_session(tmp_path, monkeypatch)
    render_state.save(
        novel_dir,
        "ch1",
        {
            "chapter_id": "ch1",
            "shots": {
                "0": {
                    "storyboard_id": 0, "workflow": "qwen_t2i", "prompt": "keep",
                    "ref_images": [], "subjects": [],
                    "candidates": ["/a.png"], "selected": "/a.png",
                    "status": "done", "error": None,
                }
            },
        },
    )

    session.enqueue_reroll(0, prompt=None)

    data = render_state.load(novel_dir, "ch1")
    assert data["shots"]["0"]["prompt"] == "keep"
    assert session._queue[-1]["prompt"] == "keep"


def test_seed_pending_resets_orphaned_rendering_when_worker_dead(tmp_path, monkeypatch):
    """worker 已死时，陈旧 rendering 态复位 pending 并重新入队（不静默卡死）。"""
    session, novel_dir = _make_session(tmp_path, monkeypatch)
    render_state.save(
        novel_dir,
        "ch1",
        {
            "chapter_id": "ch1",
            "shots": {
                "0": {
                    "storyboard_id": 0, "workflow": "qwen_t2i", "prompt": "p",
                    "ref_images": [], "subjects": [],
                    "candidates": [], "selected": None,
                    "status": "rendering", "error": None,
                }
            },
        },
    )
    # 无 worker_task → worker_alive False
    specs = [{"storyboard_id": 0, "workflow": "qwen_t2i", "prompt": "p", "ref_images": [], "subjects": []}]
    session.seed_pending(specs)

    data = render_state.load(novel_dir, "ch1")
    assert data["shots"]["0"]["status"] == "pending"  # 复位
    assert len(session._queue) == 1  # 重新入队


def test_seed_pending_dedups_already_queued(tmp_path, monkeypatch):
    """同一 shot 已在内存队列 → 不重复入队（防双倍 GPU）。"""
    session, novel_dir = _make_session(tmp_path, monkeypatch)
    render_state.save(
        novel_dir,
        "ch1",
        {"chapter_id": "ch1", "shots": {"0": {"storyboard_id": 0, "status": "pending", "selected": None}}},
    )
    spec = {"storyboard_id": 0, "workflow": "qwen_t2i", "prompt": "p", "ref_images": [], "subjects": []}
    session._queue.append(spec)  # 预置已在队列

    session.seed_pending([spec])

    assert len(session._queue) == 1  # 未重复入队

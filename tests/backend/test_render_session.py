"""RenderSession 单测：聚焦不触发真实 GPU worker 的纯逻辑分支。"""

from pathlib import Path

from novel2media import render_state
from novel2media.nodes.setup_nodes import write_scenes_profile


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
                    "storyboard_id": 0,
                    "workflow": "qwen_t2i",
                    "prompt": "old",
                    "ref_images": [],
                    "subjects": [],
                    "candidates": ["/a.png"],
                    "selected": "/a.png",
                    "status": "done",
                    "error": None,
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
                    "storyboard_id": 0,
                    "workflow": "qwen_t2i",
                    "prompt": "keep",
                    "ref_images": [],
                    "subjects": [],
                    "candidates": ["/a.png"],
                    "selected": "/a.png",
                    "status": "done",
                    "error": None,
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
                    "storyboard_id": 0,
                    "workflow": "qwen_t2i",
                    "prompt": "p",
                    "ref_images": [],
                    "subjects": [],
                    "candidates": [],
                    "selected": None,
                    "status": "rendering",
                    "error": None,
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


async def test_ensure_render_session_rebuilds_after_restart(tmp_path, monkeypatch):
    """#7：会话内存丢失（后端重启）时，render 端点据 checkpoint payload 惰性重建会话。"""
    import api.v1.endpoints.render as render_ep
    import services.render_session as rs

    # 桩掉真实 worker 启动 + 配置/客户端，避免连服务器
    monkeypatch.setattr(rs.RenderSession, "_ensure_worker", lambda self: None)
    monkeypatch.setattr(rs, "_load_services_config", lambda novel_dir: _FakeCfg())
    monkeypatch.setattr(rs, "ComfyUIClient", lambda *a, **k: object())
    rs._sessions.clear()  # 模拟重启后内存会话全空

    novel_dir = str(tmp_path / "novel")
    render_state.save(
        novel_dir,
        "ch1",
        {
            "chapter_id": "ch1",
            "shots": {
                "0": {
                    "storyboard_id": 0,
                    "workflow": "qwen_t2i",
                    "prompt": "p",
                    "ref_images": [],
                    "subjects": [],
                    "candidates": [],
                    "selected": None,
                    "status": "pending",
                    "error": None,
                }
            },
        },
    )

    # 桩 runner：run 仍停在 image_render（payload 随 checkpoint 持久化）
    class _Meta:
        pass

    meta = _Meta()
    meta.novel_dir = novel_dir

    async def _get_run(_):
        return meta

    async def _get_state(_):
        return {
            "active_interaction": {
                "payload": {
                    "type": "image_render",
                    "chapter_id": "ch1",
                    "storyboard": [{"storyboard_id": 0, "scene_change": True, "scene_prompt": "p", "subjects": []}],
                    "specs": [
                        {"storyboard_id": 0, "workflow": "qwen_t2i", "prompt": "p", "ref_images": [], "subjects": []}
                    ],
                }
            }
        }

    monkeypatch.setattr(render_ep.runner, "get_run", _get_run)
    monkeypatch.setattr(render_ep.runner, "get_current_run_state", _get_state)

    assert rs.get_session("run-x") is None  # 重启后无会话
    session = await render_ep._ensure_render_session("run-x")
    assert session is not None  # 已惰性重建
    assert rs.get_session("run-x") is session
    assert len(session._queue) == 1  # pending shot 已重新入队续跑


async def test_ensure_render_session_returns_none_when_not_rendering(tmp_path, monkeypatch):
    """非渲染阶段 → 不重建（返回 None，端点据此 409）。"""
    import api.v1.endpoints.render as render_ep
    import services.render_session as rs

    rs._sessions.clear()

    class _Meta:
        novel_dir = str(tmp_path / "novel")

    async def _get_run(_):
        return _Meta()

    async def _get_state(_):
        return {"active_interaction": {"payload": {"type": "audio_config"}}}

    monkeypatch.setattr(render_ep.runner, "get_run", _get_run)
    monkeypatch.setattr(render_ep.runner, "get_current_run_state", _get_state)

    assert await render_ep._ensure_render_session("run-y") is None


# ─── 场景锚点补位 _apply_scene（角色优先、2 图预算、幂等）──────────────────────


def _seed_scene_plate(novel_dir, scene_id="陆家", build_asset=True):
    """落一张已生成的空景板 + scenes_profile（ref_image 非空 → _apply_scene 复用不生成）。"""
    scenes_dir = Path(novel_dir) / "scenes"
    scenes_dir.mkdir(parents=True, exist_ok=True)
    (scenes_dir / f"{scene_id}.png").write_bytes(b"plate")
    write_scenes_profile(
        novel_dir,
        {scene_id: {"name": scene_id, "build_asset": build_asset, "ref_image": f"scenes/{scene_id}.png"}},
    )


async def test_apply_scene_upgrades_t2i_to_edit_when_no_chars(tmp_path, monkeypatch):
    """0 角色镜头：场景锚点占 slot1 → workflow 升级 qwen_edit；幂等不重复补。"""
    session, novel_dir = _make_session(tmp_path, monkeypatch)
    _seed_scene_plate(novel_dir)

    job = {"storyboard_id": 0, "workflow": "qwen_t2i", "ref_images": [], "scene_id": "陆家"}
    await session._apply_scene(job)

    assert job["workflow"] == "qwen_edit"
    assert len(job["ref_images"]) == 1
    assert job["ref_images"][0].endswith("/scenes/陆家.png")

    await session._apply_scene(job)  # 幂等
    assert len(job["ref_images"]) == 1


async def test_apply_scene_char_first_scene_fills_second_slot(tmp_path, monkeypatch):
    """1 角色镜头：角色 ref 优先占 slot1，场景锚点补 slot2。"""
    session, novel_dir = _make_session(tmp_path, monkeypatch)
    _seed_scene_plate(novel_dir)

    job = {"storyboard_id": 1, "workflow": "qwen_edit", "ref_images": ["/char.png"], "scene_id": "陆家"}
    await session._apply_scene(job)

    assert len(job["ref_images"]) == 2
    assert job["ref_images"][0] == "/char.png"  # 角色仍在 slot1
    assert job["ref_images"][1].endswith("/scenes/陆家.png")  # 场景补 slot2


async def test_apply_scene_no_slot_when_two_chars(tmp_path, monkeypatch):
    """2 角色镜头：2 图预算用尽 → 本期不补场景（等扩到第 3 张参考图）。"""
    session, novel_dir = _make_session(tmp_path, monkeypatch)
    _seed_scene_plate(novel_dir)

    job = {"storyboard_id": 2, "workflow": "qwen_edit", "ref_images": ["/a.png", "/b.png"], "scene_id": "陆家"}
    await session._apply_scene(job)

    assert job["ref_images"] == ["/a.png", "/b.png"]  # 不变


async def test_apply_scene_skips_non_build_asset(tmp_path, monkeypatch):
    """一次性地点（build_asset=False）：不补场景锚点，照旧走文本背景。"""
    session, novel_dir = _make_session(tmp_path, monkeypatch)
    _seed_scene_plate(novel_dir, build_asset=False)

    job = {"storyboard_id": 3, "workflow": "qwen_t2i", "ref_images": [], "scene_id": "陆家"}
    await session._apply_scene(job)

    assert job["workflow"] == "qwen_t2i"
    assert job["ref_images"] == []


async def test_apply_scene_noop_without_scene_id(tmp_path, monkeypatch):
    """无 scene_id（老稿件/纯特写）：不补场景锚点。"""
    session, novel_dir = _make_session(tmp_path, monkeypatch)
    _seed_scene_plate(novel_dir)

    job = {"storyboard_id": 4, "workflow": "qwen_t2i", "ref_images": [], "scene_id": ""}
    await session._apply_scene(job)

    assert job["workflow"] == "qwen_t2i"
    assert job["ref_images"] == []


async def test_commit_candidate_increments_index_and_default_selects(tmp_path, monkeypatch):
    """#9：候选落盘+追加单次锁内读写——序号递增不覆盖旧候选，首张默认选中。"""
    session, novel_dir = _make_session(tmp_path, monkeypatch)
    render_state.save(
        novel_dir,
        "ch1",
        {
            "chapter_id": "ch1",
            "shots": {
                "0": {"storyboard_id": 0, "candidates": [], "selected": None, "status": "pending", "error": None}
            },
        },
    )

    p0, sel0 = await session._commit_candidate("0", 0, "out.png", b"img0")
    p1, sel1 = await session._commit_candidate("0", 0, "out.png", b"img1")

    # 序号递增、文件名不同（不覆盖）
    assert p0.endswith("shot_0_cand_00.png")
    assert p1.endswith("shot_0_cand_01.png")
    assert sel0 == p0 and sel1 == p0  # 首张默认选中，后续不自动改选
    data = render_state.load(novel_dir, "ch1")
    shot = data["shots"]["0"]
    assert shot["candidates"] == [p0, p1]
    assert shot["selected"] == p0
    assert shot["status"] == "done"

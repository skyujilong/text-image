"""services.workspace 隔离模块单测。"""

from __future__ import annotations

from pathlib import Path

import pytest
import services.workspace as workspace


@pytest.fixture
def runs_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把 RUNS_WORKSPACE_ROOT 指到 tmp，避免污染真实 data/runs。"""
    root = tmp_path / "runs"
    monkeypatch.setenv("RUNS_WORKSPACE_ROOT", str(root))
    return root


def _make_source(tmp_path: Path) -> Path:
    """构造一个既含输入又含产出的源小说目录。"""
    src = tmp_path / "source" / "小说A"
    (src / "chapters").mkdir(parents=True)
    (src / "chapters" / "chapter_001.txt").write_text("正文一", encoding="utf-8")
    (src / "chapters" / "chapter_002.txt").write_text("正文二", encoding="utf-8")
    (src / "config").mkdir()
    (src / "config" / "services.json").write_text("{}", encoding="utf-8")
    (src / "config.json").write_text('{"novel_title": "标题"}', encoding="utf-8")
    # 预置参考图（输入）+ profile 产出（不该 copy）
    (src / "characters").mkdir()
    (src / "characters" / "小说A-主角.png").write_bytes(b"img")
    (src / "characters" / "characters_profile.json").write_text("{}", encoding="utf-8")
    # 章节产出（不该 copy）
    (src / "ch0001" / "images").mkdir(parents=True)
    (src / "ch0001" / "images" / "shot_1_cand_00.png").write_bytes(b"out")
    (src / "ch0001" / "render_state.json").write_text("{}", encoding="utf-8")
    (src / "ch0001" / "audio.wav").write_bytes(b"wav")
    (src / "export").mkdir()
    (src / "export" / "jianying_draft.json").write_text("{}", encoding="utf-8")
    return src


def test_provision_copies_only_inputs(tmp_path: Path, runs_root: Path):
    src = _make_source(tmp_path)
    dst = workspace.provision_run_workspace("run-x", str(src))

    assert dst == runs_root / "run-x"
    # 输入被 copy
    assert (dst / "chapters" / "chapter_001.txt").read_text(encoding="utf-8") == "正文一"
    assert (dst / "config" / "services.json").is_file()
    assert (dst / "config.json").is_file()
    assert (dst / "characters" / "小说A-主角.png").is_file()
    # 产出不被 copy
    assert not (dst / "characters" / "characters_profile.json").exists()
    assert not (dst / "ch0001").exists()
    assert not (dst / "export").exists()
    # 源目录纹丝不动
    assert (src / "ch0001" / "audio.wav").is_file()


def test_provision_requires_chapters(tmp_path: Path, runs_root: Path):
    src = tmp_path / "empty"
    src.mkdir()
    with pytest.raises(FileNotFoundError):
        workspace.provision_run_workspace("run-y", str(src))


def test_provision_rejects_non_dir(tmp_path: Path, runs_root: Path):
    with pytest.raises(NotADirectoryError):
        workspace.provision_run_workspace("run-z", str(tmp_path / "nope"))


def test_provision_rejects_collision(tmp_path: Path, runs_root: Path):
    src = _make_source(tmp_path)
    workspace.provision_run_workspace("run-dup", str(src))
    with pytest.raises(FileExistsError):
        workspace.provision_run_workspace("run-dup", str(src))


def test_is_within_workspace(tmp_path: Path, runs_root: Path):
    inside = workspace.run_workspace_dir("run-a")
    assert workspace.is_within_workspace(inside) is True
    assert workspace.is_within_workspace(tmp_path / "source" / "小说A") is False


def test_delete_run_workspace_only_within_root(tmp_path: Path, runs_root: Path):
    src = _make_source(tmp_path)
    dst = workspace.provision_run_workspace("run-del", str(src))
    assert dst.exists()
    workspace.delete_run_workspace("run-del")
    assert not dst.exists()
    # legacy run：工作副本不存在 → no-op，不抛
    workspace.delete_run_workspace("legacy-run")
    assert src.is_dir()  # 源永不被碰


def test_clone_run_workspace_full_copy(tmp_path: Path, runs_root: Path):
    src = _make_source(tmp_path)
    parent = workspace.provision_run_workspace("run-parent", str(src))
    # 模拟父 run 产生了产出
    (parent / "ch0001").mkdir()
    (parent / "ch0001" / "render_state.json").write_text("{}", encoding="utf-8")

    forked = workspace.clone_run_workspace("run-fork", str(parent))
    assert (forked / "chapters" / "chapter_001.txt").is_file()
    assert (forked / "ch0001" / "render_state.json").is_file()  # 产出也带过来


def test_rewrite_abs_prefix_in_json_artifacts(tmp_path: Path, runs_root: Path):
    work = runs_root / "run-fork"
    (work / "ch0001").mkdir(parents=True)
    old, new = "/old/parent", str(work)
    (work / "ch0001" / "render_state.json").write_text(
        f'{{"selected": "{old}/ch0001/images/shot.png"}}', encoding="utf-8"
    )
    (work / "chapters_status.json").write_text(f'{{"p": "{old}/x"}}', encoding="utf-8")

    workspace.rewrite_abs_prefix_in_json_artifacts(work, old, new)
    assert old not in (work / "ch0001" / "render_state.json").read_text(encoding="utf-8")
    assert new in (work / "ch0001" / "render_state.json").read_text(encoding="utf-8")
    assert old not in (work / "chapters_status.json").read_text(encoding="utf-8")

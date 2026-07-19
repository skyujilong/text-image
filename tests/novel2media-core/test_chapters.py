"""章节分组契约层纯函数测试（build_chapter_groups / group_label / read_group_text 等）。"""
from __future__ import annotations

from pathlib import Path

from novel2media.chapters import (
    build_chapter_groups,
    chapter_pad_width,
    forward_chapter_paths,
    group_id_for,
    group_label,
    list_chapter_files,
    read_group_text,
)

SEVEN_STEMS = [
    "chapter_001_a",
    "chapter_002_b",
    "chapter_003_c",
    "chapter_004_d",
    "chapter_005_e",
    "chapter_006_f",
    "chapter_007_g",
]


# ── build_chapter_groups：切组数学 ──────────────────────────────────────────


def test_build_chapter_groups_n3():
    groups = build_chapter_groups(SEVEN_STEMS, 3)
    sizes = [len(m) for m in groups.values()]
    assert sizes == [3, 3, 1]
    assert list(groups) == ["ch0001-0003", "ch0004-0006", "ch0007"]


def test_build_chapter_groups_n1_all_single():
    groups = build_chapter_groups(SEVEN_STEMS, 1)
    assert len(groups) == 7
    assert all(len(m) == 1 for m in groups.values())
    assert list(groups) == [
        "ch0001",
        "ch0002",
        "ch0003",
        "ch0004",
        "ch0005",
        "ch0006",
        "ch0007",
    ]


def test_build_chapter_groups_n5_seven_chapters():
    groups = build_chapter_groups(SEVEN_STEMS, 5)
    sizes = [len(m) for m in groups.values()]
    assert sizes == [5, 2]
    assert list(groups) == ["ch0001-0005", "ch0006-0007"]


def test_build_chapter_groups_clamp_zero_to_one():
    groups = build_chapter_groups(SEVEN_STEMS, 0)
    assert [len(m) for m in groups.values()] == [1] * 7


def test_build_chapter_groups_clamp_large_to_five():
    groups = build_chapter_groups(SEVEN_STEMS, 99)
    assert [len(m) for m in groups.values()] == [5, 2]


def test_build_chapter_groups_sorts_unordered_input():
    unordered = ["chapter_010_x", "chapter_002_b", "chapter_001_a"]
    groups = build_chapter_groups(unordered, 1)
    # 章号序 1,2,10 —— 而非字符串序
    assert list(groups) == ["ch0001", "ch0002", "ch0010"]


# ── id 格式与位宽 ────────────────────────────────────────────────────────────


def test_pad_width_default_four_under_10k():
    assert chapter_pad_width(SEVEN_STEMS) == 4


def test_group_id_default_four_digits():
    groups = build_chapter_groups(SEVEN_STEMS, 3)
    assert "ch0001-0003" in groups


def test_pad_width_thousand_still_four():
    stems = [f"chapter_{n:03d}_x" for n in (1, 500, 1000)]
    assert chapter_pad_width(stems) == 4
    groups = build_chapter_groups(stems, 3)
    assert list(groups) == ["ch0001-1000"]


def test_pad_width_ten_thousand_is_five():
    stems = [f"chapter_{n}_x" for n in (1, 5000, 10000)]
    assert chapter_pad_width(stems) == 5
    groups = build_chapter_groups(stems, 3)
    assert list(groups) == ["ch00001-10000"]


def test_group_dict_order_equals_chapter_order():
    groups = build_chapter_groups(SEVEN_STEMS, 3)
    assert list(groups) == sorted(groups)


def test_group_id_for_single_and_multi():
    assert group_id_for(["chapter_007_g"], 4) == "ch0007"
    assert group_id_for(["chapter_001_a", "chapter_003_c"], 4) == "ch0001-0003"


# ── group_label ─────────────────────────────────────────────────────────────


def test_group_label_single():
    assert group_label(["chapter_007_g"]) == "第7章"


def test_group_label_multi():
    assert group_label(["chapter_001_a", "chapter_002_b", "chapter_003_c"]) == "第1-3章"


# ── read_group_text ─────────────────────────────────────────────────────────


def test_read_group_text_concatenates_in_order(tmp_path):
    first = tmp_path / "chapter_001_a.txt"
    second = tmp_path / "chapter_002_b.txt"
    first.write_text("第一段内容", encoding="utf-8")
    second.write_text("第二段内容", encoding="utf-8")

    text = read_group_text([str(first), str(second)])

    assert text == "第一段内容\n\n第二段内容"
    assert text.index("第一段内容") < text.index("第二段内容")
    assert "\n\n" in text


def test_read_group_text_empty_raises():
    import pytest

    with pytest.raises(ValueError):
        read_group_text([])


# ── list_chapter_files：逐章列表（阅读接口数据源）──────────────────────────


def test_list_chapter_files_sorted_by_chapter_number(tmp_path):
    """按章号数字序，chapter_10 排在 chapter_2 之后（非字符串序）。"""
    chapters_dir = tmp_path / "chapters"
    chapters_dir.mkdir()
    (chapters_dir / "chapter_10_y.txt").write_text("十", encoding="utf-8")
    (chapters_dir / "chapter_2_x.txt").write_text("二", encoding="utf-8")

    files = list_chapter_files(tmp_path)

    assert [f["number"] for f in files] == [2, 10]
    assert [f["label"] for f in files] == ["第2章", "第10章"]
    assert [f["stem"] for f in files] == ["chapter_2_x", "chapter_10_y"]


def test_list_chapter_files_missing_dir_returns_empty(tmp_path):
    """chapters 目录不存在或无 .txt → 返回 []。"""
    assert list_chapter_files(tmp_path) == []
    (tmp_path / "chapters").mkdir()
    assert list_chapter_files(tmp_path) == []


# ── forward_chapter_paths：新角色触发式后瞻窗口 ──────────────────────────────


def _make_chapters(tmp_path, count):
    chapters_dir = tmp_path / "chapters"
    chapters_dir.mkdir()
    for i in range(1, count + 1):
        (chapters_dir / f"chapter_{i:03d}_t.txt").write_text(f"第{i}章", encoding="utf-8")
    return chapters_dir


def test_forward_chapter_paths_slices_next_k(tmp_path):
    """取当前组末章之后的 K 章路径（re-glob 现盘）。"""
    _make_chapters(tmp_path, 6)
    paths = forward_chapter_paths(tmp_path, ["chapter_002_t"], 3)
    assert [Path(p).stem for p in paths] == ["chapter_003_t", "chapter_004_t", "chapter_005_t"]
    # 拼读得到后续三章内容
    assert read_group_text(paths) == "第3章\n\n第4章\n\n第5章"


def test_forward_chapter_paths_multi_member_group_uses_last(tmp_path):
    """当前组多章时按最大章号定位，取其后 K 章。"""
    _make_chapters(tmp_path, 6)
    paths = forward_chapter_paths(tmp_path, ["chapter_001_t", "chapter_002_t", "chapter_003_t"], 2)
    assert [Path(p).stem for p in paths] == ["chapter_004_t", "chapter_005_t"]


def test_forward_chapter_paths_tail_and_zero_k(tmp_path):
    """到全书末尾不足 K → 截断；k<=0 或空成员 → []。"""
    _make_chapters(tmp_path, 4)
    assert [Path(p).stem for p in forward_chapter_paths(tmp_path, ["chapter_003_t"], 3)] == ["chapter_004_t"]
    assert forward_chapter_paths(tmp_path, ["chapter_004_t"], 3) == []  # 已是末章
    assert forward_chapter_paths(tmp_path, ["chapter_001_t"], 0) == []  # 关闭后瞻
    assert forward_chapter_paths(tmp_path, [], 3) == []  # 空成员


def test_forward_chapter_paths_prefers_ordered_stems(tmp_path):
    """优先用传入的 ordered_stems（chapter_groups 展平）定位，不依赖现盘 glob。"""
    (tmp_path / "chapters").mkdir()  # 目录存在但为空，证明未走 glob
    ordered = ["chapter_001_a", "chapter_002_b", "chapter_003_c", "chapter_004_d"]
    paths = forward_chapter_paths(tmp_path, ["chapter_002_b"], 5, ordered_stems=ordered)
    assert [Path(p).stem for p in paths] == ["chapter_003_c", "chapter_004_d"]


# ── _discover_new_single_chapter_groups：id 碰撞不覆盖既有组 ──────────────────


def test_discover_new_single_group_id_collision_preserves_existing(tmp_path):
    """新文件算出与既有组相同的单元 id 时，不覆盖既有组（对齐 build_chapter_groups 暴露意图）。"""
    from novel2media.nodes.chapter_nodes import _discover_new_single_chapter_groups

    chapters_dir = tmp_path / "chapters"
    chapters_dir.mkdir()
    # 既有组 ch0001 的成员是原始文件；新文件章号同为 1 → 算出相同 gid ch0001。
    (chapters_dir / "chapter_001_new_dup.txt").write_text("新增冲突章", encoding="utf-8")

    chapter_groups = {"ch0001": ["chapter_001_original"]}
    chapters_status = {"ch0001": "done"}

    _discover_new_single_chapter_groups(chapters_dir, chapter_groups, chapters_status, 4)

    # 既有组成员与状态均不被替换
    assert chapter_groups["ch0001"] == ["chapter_001_original"]
    assert chapters_status["ch0001"] == "done"
    assert "chapter_001_new_dup" not in chapter_groups["ch0001"]

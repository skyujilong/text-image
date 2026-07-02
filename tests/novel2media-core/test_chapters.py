"""章节分组契约层纯函数测试（build_chapter_groups / group_label / read_group_text 等）。"""
from __future__ import annotations

from novel2media.chapters import (
    build_chapter_groups,
    chapter_pad_width,
    group_id_for,
    group_label,
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

"""章节文件命名与排序约定。

章节文件由用户预先按 `chapters/chapter_xxx_ssss.txt` 整理（xxx 为章序数字，
ssss 为章节标题/标识）。排序按 xxx 数字序，避免字符串序导致
`chapter_10` 排在 `chapter_2` 之前。
"""
from __future__ import annotations

import re
from pathlib import Path

# pydantic/FastAPI 在 Python <3.12 上要求用 typing_extensions.TypedDict（作为响应模型解析），
# 该包由 pydantic 传递依赖保证可用；用它在各 Python 版本下都成立。
from typing_extensions import TypedDict

from novel2media_logging import get_logger

log = get_logger(__name__)

_CHAPTER_NUM_RE = re.compile(r"chapter_(\d+)", re.IGNORECASE)

# 合并粒度合法区间：单章(1) ~ 最多五章(5)一组。
_MIN_GROUP_SIZE = 1
_MAX_GROUP_SIZE = 5

# 单元 id 零填充最小位宽（<1万章时固定 4 位）。
_MIN_PAD_WIDTH = 4


def chapter_sort_key(ch_id: str) -> tuple[int, str]:
    """章节排序键：优先按 `chapter_xxx` 中的数字序，无数字则退回字符串序。

    ch_id 既可为文件名 stem（如 "chapter_02_初入江湖"），也可为纯
    "chapter_02"；两种均按数字 2 排序。
    """
    m = _CHAPTER_NUM_RE.search(ch_id)
    return (int(m.group(1)) if m else 0, ch_id)


def chapter_number(stem: str) -> int:
    """从章节文件名 stem 解析章号（`chapter_sort_key` 的数字部分）。

    无法解析出数字时返回 0（与 `chapter_sort_key` 一致）。
    """
    return chapter_sort_key(stem)[0]


def chapter_pad_width(file_stems: list[str]) -> int:
    """单元 id 零填充位宽：`W = max(4, 最大章号的十进制位数)`。

    空列表返回 4。破千章 → 4 位；破万章 → 5 位。字典序据此 == 章号序。
    """
    if not file_stems:
        return _MIN_PAD_WIDTH
    max_num = max(chapter_number(stem) for stem in file_stems)
    return max(_MIN_PAD_WIDTH, len(str(max_num)))


def group_id_for(members: list[str], pad_width: int) -> str:
    """合成单元 id：members 已按章序排列。

    单成员 → `ch<n>`；多成员 → `ch<首>-<止>`，章号按 pad_width 零填充。
    """
    if not members:
        raise ValueError("group_id_for: members 不能为空")
    first = chapter_number(members[0])
    last = chapter_number(members[-1])
    if len(members) == 1:
        return f"ch{first:0{pad_width}d}"
    return f"ch{first:0{pad_width}d}-{last:0{pad_width}d}"


def build_chapter_groups(
    file_stems: list[str],
    group_size: int,
    pad_width: int | None = None,
) -> dict[str, list[str]]:
    """把有序章节文件切成「连续 group_size 章一组」的单元。

    - 先按 `chapter_sort_key` 排序 file_stems。
    - group_size 防御性 clamp 到 1..5。
    - pad_width 为 None 时用 `chapter_pad_width` 计算。
    - 连续 group_size 个为一组，末组不足自成一组。
    - 解析不出数字的 stem（章号 0）用「排序后位置序号(1-based)」代替其章号
      参与 id 计算，并 log.warning 暴露（不符合 chapter_xxx_* 约定）。
    - 返回有序 dict：`group_id -> [成员 stem, ...]`（组按章序）。
    - 若两组算出相同 id（异常，如章号重复）抛 ValueError 暴露。
    """
    size = max(_MIN_GROUP_SIZE, min(_MAX_GROUP_SIZE, group_size))
    ordered = sorted(file_stems, key=chapter_sort_key)
    if pad_width is None:
        pad_width = chapter_pad_width(ordered)

    # 解析不出数字的 stem 用「排序后 1-based 位置序号」当章号，避免 id 撞 ch0000。
    numbered: list[str] = []
    for idx, stem in enumerate(ordered, start=1):
        if chapter_number(stem) == 0:
            log.warning(
                "chapter stem 无法解析章号，退回排序位置序号",
                stem=stem,
                fallback_number=idx,
            )
            numbered.append(f"chapter_{idx:0{pad_width}d}_{stem}")
        else:
            numbered.append(stem)

    groups: dict[str, list[str]] = {}
    for start in range(0, len(numbered), size):
        id_members = numbered[start : start + size]
        real_members = ordered[start : start + size]
        group_id = group_id_for(id_members, pad_width)
        if group_id in groups:
            raise ValueError(f"分组 id 冲突（章号可能重复）: {group_id}")
        groups[group_id] = real_members
    return groups


def group_label(members: list[str]) -> str:
    """单元人读标签：单章 `第{n}章`；多章 `第{first}-{last}章`。"""
    if not members:
        raise ValueError("group_label: members 不能为空")
    first = chapter_number(members[0])
    last = chapter_number(members[-1])
    if len(members) == 1:
        return f"第{first}章"
    return f"第{first}-{last}章"


def read_group_text(paths: list[str]) -> str:
    """按顺序读取每个绝对路径 `.txt`（utf-8），用 `\\n\\n` 拼接返回。

    空列表抛 ValueError（不应发生）。
    """
    if not paths:
        raise ValueError("read_group_text: paths 不能为空")
    return "\n\n".join(Path(p).read_text(encoding="utf-8") for p in paths)


class ChapterFileInfo(TypedDict):
    """一个原始章节文件的可读元信息（供前端逐章列表）。"""

    stem: str
    number: int
    label: str


def list_chapter_files(novel_dir: str | Path) -> list[ChapterFileInfo]:
    """列 `<novel_dir>/chapters/*.txt`，按章序返回逐章元信息。

    - chapters 目录不存在或无 `.txt` → 返回 `[]`。
    - number 取 `chapter_number(stem)`（解析不出为 0）；label 用 `group_label([stem])` → `第{n}章`。
    - 按 `chapter_sort_key` 排序（数字序，`chapter_10` 排在 `chapter_2` 之后）。
    """
    chapters_dir = Path(novel_dir) / "chapters"
    if not chapters_dir.is_dir():
        return []
    stems = sorted((p.stem for p in chapters_dir.glob("*.txt")), key=chapter_sort_key)
    return [
        {"stem": stem, "number": chapter_number(stem), "label": group_label([stem])}
        for stem in stems
    ]


def forward_chapter_paths(
    novel_dir: str | Path,
    current_members: list[str],
    k: int,
    *,
    ordered_stems: list[str] | None = None,
) -> list[str]:
    """当前处理组之后的 K 个章节文件绝对路径（供「新角色触发式后瞻」读原文）。

    用途：检测到本组有新角色时，多读后续 K 章上下文，让首建档的外观/真名/别名更完整
    （见 detect_new_characters_llm）。到全书末尾或 k<=0 时返回 []。

    - current_members：当前组成员 stem（load_chapter 存的 current_chapter_member_paths 取 .stem）。
    - ordered_stems：全书有序 stem 列表。**优先由调用方从 shared 的 chapter_groups 展平传入**
      （chapter_files/chapter_order 不在 _SHARED_FIELDS、进不了 plan 子图）；None 时回退
      glob `<novel_dir>/chapters` 现盘（list_chapter_files）。
    - 定位方式按章号：取当前组最大章号，选章号更大的前 K 个 stem（对多章组、非连续章号都稳）。
    - 返回 `<novel_dir>/chapters/<stem>.txt` 绝对路径，交给 read_group_text 拼读。
    """
    if k <= 0 or not current_members:
        return []
    if ordered_stems is None:
        ordered = [info["stem"] for info in list_chapter_files(novel_dir)]
    else:
        ordered = sorted(ordered_stems, key=chapter_sort_key)

    current_last_num = max(chapter_number(m) for m in current_members)
    after = [s for s in ordered if chapter_number(s) > current_last_num][:k]

    chapters_dir = Path(novel_dir) / "chapters"
    return [str(chapters_dir / f"{stem}.txt") for stem in after]

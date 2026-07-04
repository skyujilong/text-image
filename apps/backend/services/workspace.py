"""每-run 产出隔离的唯一真源。

模型：用户的「源小说目录」（source_dir）只读，绝不写；每个 run 在
`RUNS_WORKSPACE_ROOT/<run_id>/` 下有独立工作副本，`novel_dir` 指向它。
建 run 时把源的**输入**白名单 copy 进工作副本，之后所有产出（图片/音频/
render_state/export…）都落工作副本，源目录纹丝不动。同名小说、同书多 run
天然不冲突。

不 import graph_runner（graph_runner 反向 import 本模块），避免循环依赖。
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
_DATA_DIR = _PROJECT_ROOT / "data"

# copy 白名单：判定源目录下哪些是「输入」（可 copy）vs「产出」（绝不 copy 进新 run）
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
# characters/ 下这些是产出，即便是 json 也不当输入
_CHAR_OUTPUT_NAMES = {"characters_profile.json"}


def runs_workspace_root() -> Path:
    """每-run 工作副本的根目录（可配置，默认 data/runs），确保存在。"""
    root = Path(os.environ.get("RUNS_WORKSPACE_ROOT", str(_DATA_DIR / "runs")))
    root.mkdir(parents=True, exist_ok=True)
    return root


def run_workspace_dir(run_id: str) -> Path:
    """某 run 的工作副本目录 = <root>/<run_id>/（不保证存在）。"""
    return runs_workspace_root() / run_id


def provision_run_workspace(run_id: str, source_dir: str) -> Path:
    """把源小说的输入白名单 copy 进 run 的工作副本，返回工作副本路径。

    白名单（只 copy 输入，绝不带进任何产出）：
      - chapters/            必需（无 chapters/*.txt 抛 FileNotFoundError）
      - config/              可选（services.json / novel.json 覆盖）
      - config.json          可选（getNovelConfig 预填用）
      - characters/*.<img>   可选（用户预置的三视图参考图；排除 profile 产出）

    源目录不是目录 → NotADirectoryError；工作副本已存在（run_id 撞车）→ FileExistsError。
    这些异常由端点层映射为 400。
    """
    src = Path(source_dir)
    if not src.is_dir():
        raise NotADirectoryError(f"source_dir 不是目录: {src}")
    chapters = src / "chapters"
    if not chapters.is_dir() or not any(chapters.glob("*.txt")):
        raise FileNotFoundError(f"source_dir 下缺少 chapters/*.txt: {src}")

    dst = run_workspace_dir(run_id)
    if dst.exists():
        raise FileExistsError(f"工作副本已存在: {dst}")
    dst.mkdir(parents=True)

    # 1) chapters/（必需）
    shutil.copytree(chapters, dst / "chapters", symlinks=False)
    # 2) config/（可选）
    if (src / "config").is_dir():
        shutil.copytree(src / "config", dst / "config", symlinks=False)
    # 3) 根 config.json（可选）
    if (src / "config.json").is_file():
        shutil.copy2(src / "config.json", dst / "config.json")
    # 4) characters/ 下的图片文件（可选预置参考图），逐文件 copy、排除产出
    src_chars = src / "characters"
    if src_chars.is_dir():
        out_chars = dst / "characters"
        out_chars.mkdir(exist_ok=True)
        for f in src_chars.iterdir():
            if f.is_file() and f.suffix.lower() in _IMAGE_EXTS and f.name not in _CHAR_OUTPUT_NAMES:
                shutil.copy2(f, out_chars / f.name)
    return dst


def is_within_workspace(path: str | Path) -> bool:
    """path 是否在 RUNS_WORKSPACE_ROOT 内——删除前的安全守护，防止误删源目录。"""
    try:
        Path(path).resolve().relative_to(runs_workspace_root().resolve())
        return True
    except ValueError:
        return False


def delete_run_workspace(run_id: str) -> None:
    """删除某 run 的工作副本；仅当它落在 RUNS_WORKSPACE_ROOT 内才动手。

    legacy run 的 novel_dir 指向源目录、不在 root 内 → 此处 run_workspace_dir 不存在，
    天然 no-op，源目录永不被删。
    """
    d = run_workspace_dir(run_id)
    if d.exists() and is_within_workspace(d):
        shutil.rmtree(d, ignore_errors=True)


def clone_run_workspace(new_run_id: str, parent_novel_dir: str) -> Path:
    """fork：整树 copy 父 run 的工作副本（含产出），返回新工作副本路径。

    fork 从历史 checkpoint 续跑、需沿用父已渲染的产出，故整树而非白名单。
    """
    dst = run_workspace_dir(new_run_id)
    if dst.exists():
        raise FileExistsError(f"工作副本已存在: {dst}")
    shutil.copytree(parent_novel_dir, dst, symlinks=False)
    return dst


# fork copy 后需修正绝对路径的文件产出（JSON 里烘死了旧 novel_dir 前缀）
def rewrite_abs_prefix_in_json_artifacts(work_dir: Path, old_prefix: str, new_prefix: str) -> None:
    """机械替换 work_dir 内文件产出里的旧 novel_dir 前缀为新前缀。

    覆盖 render_state.json / timeline.json / chapters_status.json / export/jianying_draft.json。
    仅在 fork（copy 父目录）后调用；源前缀是唯一绝对路径，不会误伤。
    """
    targets = [
        *work_dir.glob("*/render_state.json"),
        *work_dir.glob("*/timeline.json"),
        work_dir / "chapters_status.json",
        work_dir / "export" / "jianying_draft.json",
    ]
    for p in targets:
        if not p.is_file():
            continue
        txt = p.read_text(encoding="utf-8")
        if old_prefix in txt:
            p.write_text(txt.replace(old_prefix, new_prefix), encoding="utf-8")

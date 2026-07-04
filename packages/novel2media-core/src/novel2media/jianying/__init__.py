"""剪映（JianYing Pro）草稿生成。

把 run 的产物（每章 audio.wav + timeline.json 图片落位 + subtitles.srt 字幕）
组装成一个可被最新版剪映打开的**明文草稿工程**（draft_content.json + draft_meta_info.json）。
"""

from novel2media.jianying.draft_builder import (
    build_jianying_draft,
    detect_jianying_drafts_dir,
    install_draft_to_jianying,
)

__all__ = [
    "build_jianying_draft",
    "detect_jianying_drafts_dir",
    "install_draft_to_jianying",
]

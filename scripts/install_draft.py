"""复用 install_draft_to_jianying 把已生成的草稿装到非沙盒的剪映草稿目录。

区别 smoke_export_jianying.py：这个脚本假设草稿已经生成，只做「装入」这一步；
它会调 install_draft_to_jianying，正确改写 draft_content.json 里的绝对路径。
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "apps" / "backend"))  # noqa: E402

from novel2media.jianying import install_draft_to_jianying

# 已生成的草稿源目录（build_jianying_draft 输出位置）
SRC_DRAFT = "/Users/nbe01/workspace/text-image/data/runs/无限疯神试炼-无限疯神试炼-770097/export/jianying/novel2media"

# 非沙盒剪映的草稿根目录 —— drafts_root == app_visible_root
JIANYING_DRAFTS = str(Path.home() / "Movies" / "JianyingPro" / "User Data" / "Projects" / "com.lveditor.draft")


def main() -> None:
    src = Path(SRC_DRAFT)
    if not src.exists():
        raise SystemExit(f"源草稿不存在: {SRC_DRAFT}\n请先跑 scripts/smoke_export_jianying.py 生成")

    target_root = Path(JIANYING_DRAFTS)
    if not target_root.exists():
        raise SystemExit(
            f"剪映草稿目录不存在: {JIANYING_DRAFTS}\n非沙盒剪映应该有这个目录，请确认剪映已安装且至少启动过一次"
        )

    print(f"[src ] {SRC_DRAFT}")
    print(f"[dest] {JIANYING_DRAFTS}")

    # 非沙盒环境两者相同：真实磁盘路径 == 剪映视角路径
    dest = install_draft_to_jianying(SRC_DRAFT, JIANYING_DRAFTS, JIANYING_DRAFTS)
    print(f"\n✅ 装入完成：{dest}")

    # 快速校验：读回 draft_content.json 看第一个 audio path 是否改写成功
    import json

    content = json.loads((Path(dest) / "draft_content.json").read_text(encoding="utf-8"))
    audios = content.get("materials", {}).get("audios", [])
    videos = content.get("materials", {}).get("videos", [])
    if audios:
        print(f"[verify] audios[0].path = {audios[0].get('path')}")
    if videos:
        print(f"[verify] videos[0].path = {videos[0].get('path')}")

    mi = json.loads((Path(dest) / "draft_meta_info.json").read_text(encoding="utf-8"))
    print(f"[verify] draft_fold_path = {mi.get('draft_fold_path')}")
    print(f"[verify] draft_root_path = {mi.get('draft_root_path')}")


if __name__ == "__main__":
    main()

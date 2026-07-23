"""端到端冒烟脚本：用现有 checkpoint 产物一键组装剪映草稿，只看效果，不改任何 state。

不走 HTTP、不走后端 lifespan，直接：
  1. 从主图 checkpoint 读 chapter_order / chapters_status / chapters_artifacts
  2. 调 build_jianying_draft() 组装真·剪映草稿到 <novel_dir>/export/jianying/
  3. 不调 install_draft_to_jianying —— 不污染剪映草稿目录

用途：手工验证 audio + timeline + subtitles 能否被剪映正确解析、图片落位对不对。
运行：
  # 先停后端（uvicorn 独占 checkpoints.db）
  cd /Users/nbe01/workspace/text-image && uv run python scripts/smoke_export_jianying.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "apps" / "backend"))

RUN_ID = "无限疯神试炼-无限疯神试炼-770097"


async def main() -> None:
    import services.graph_runner as runner
    from novel2media.jianying import build_jianying_draft

    await runner.init_runner()
    try:
        meta = await runner.get_run(RUN_ID)
        assert meta is not None, f"run 不存在: {RUN_ID}"
        state = await runner.get_run_state_values(RUN_ID)

        novel_dir = meta.novel_dir
        chapter_order = state.get("chapter_order", [])
        chapters_status = state.get("chapters_status", {})
        chapters_artifacts = state.get("chapters_artifacts", {})

        print("=== 输入状态 ===")
        print(f"novel_dir       = {novel_dir}")
        print(f"chapter_order   = {chapter_order}")
        print(f"chapters_status = {chapters_status}")
        for ch, art in chapters_artifacts.items():
            print(f"artifact[{ch}] keys = {list(art.keys())}")
            print(f"           audio    = {art.get('audio_path')}")
            print(f"           timeline = {art.get('timeline_path')}")
            print(f"           subtitles= {art.get('subtitles_path')}")

        eligible = [
            ch
            for ch in (chapter_order or sorted(chapters_status.keys()))
            if chapters_status.get(ch) in ("rendered", "exported")
            and chapters_artifacts.get(ch, {}).get("audio_path")
        ]
        print(f"\n=== 可导出章节: {eligible} ===")
        if not eligible:
            raise SystemExit(
                "没有 rendered/exported 且有 audio_path 的章节 —— "
                "请先在前端把 ch0001-0002 生成过时间轴（chapters_status 会翻为 rendered）"
            )

        # 在线程池里跑 —— pyJianYingDraft 内部会加载 numpy/imageio/pymediainfo（同步且慢），
        # 且 build_jianying_draft 本身是同步函数
        draft_dir = await asyncio.to_thread(
            build_jianying_draft,
            novel_dir,
            chapter_order,
            chapters_status,
            chapters_artifacts,
        )

        draft_path = Path(draft_dir)
        print(f"\n=== 草稿输出 ===\n{draft_path}")
        assert draft_path.exists(), f"草稿目录未生成: {draft_dir}"

        content_json = draft_path / "draft_content.json"
        meta_json = draft_path / "draft_meta_info.json"
        materials = draft_path / "materials"
        assert content_json.exists(), "缺 draft_content.json"
        assert meta_json.exists(), "缺 draft_meta_info.json"
        assert materials.exists() and materials.is_dir(), "缺 materials/ 子目录"

        print(f"draft_content.json   = {content_json.stat().st_size} bytes")
        print(f"draft_meta_info.json = {meta_json.stat().st_size} bytes")

        material_files = sorted(materials.iterdir())
        print(f"materials/ 共 {len(material_files)} 个文件:")
        for p in material_files[:15]:
            print(f"  {p.name}  ({p.stat().st_size} bytes)")
        if len(material_files) > 15:
            print(f"  … 省略 {len(material_files) - 15} 个")

        # 快速内容检查：draft_content.json 应至少含 audio_track / video_track
        content = json.loads(content_json.read_text(encoding="utf-8"))
        tracks = content.get("tracks", [])
        print(f"\n=== draft_content.json.tracks: {len(tracks)} 条 ===")
        for tr in tracks:
            seg_cnt = len(tr.get("segments") or [])
            print(f"  type={tr.get('type'):8}  name={tr.get('name'):8}  segments={seg_cnt}")

        print(
            "\n✅ 草稿生成成功。手动打开方式：\n"
            "  1) 剪映客户端 → 我的草稿 → 会自动检测本机草稿目录；\n"
            "     如未装 → 拷 {draft_path} 到 ~/Movies/JianyingPro/User Data/Projects/com.lveditor.draft/"
        )
    finally:
        await runner.shutdown_runner()


if __name__ == "__main__":
    asyncio.run(main())

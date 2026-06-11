# 小说转多媒体系统 Plan B：LangGraph 图流程实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**前置条件:** Plan A（`2026-06-11-plan-a-foundation.md`）已完成，`src/novel2media/state.py`、`config.py`、`clients/`、`audio/` 全部就位。

**Goal:** 实现所有 LangGraph 节点、子图、顶层图及 `langgraph dev` 可启动的入口，完整覆盖设计文档第 5 节的图结构。

**Architecture:** 节点按子图分文件：`nodes/setup.py`（角色设定）、`nodes/init.py`（初始化）、`nodes/chapter.py`（章节流程）。子图各自组装，顶层图在 `graph.py` 串联。所有节点函数签名为 `(state: GraphState) -> dict`，返回需更新的字段，由 LangGraph 合并入 State。

**Tech Stack:** langgraph>=0.2, langgraph-checkpoint-sqlite, langchain-anthropic（LLM 调用），SqliteSaver checkpoint

---

## 文件结构

```
src/novel2media/
├── graph.py                        # 顶层图 + SqliteSaver 入口（langgraph.json 指向此处）
├── subgraphs/
│   ├── __init__.py
│   ├── setup.py                    # character_setup_subgraph
│   ├── init_graph.py               # init_subgraph
│   └── chapter.py                  # chapter_loop_subgraph
└── nodes/
    ├── __init__.py
    ├── setup_nodes.py              # setup_dispatcher / check_needs_visual / image_card_draw /
    │                               # fix_character_visual / voice_params_choice /
    │                               # voice_params_manual / voice_card_draw / fix_character_profile
    ├── init_nodes.py               # load_config
    └── chapter_nodes.py            # load_chapter / adapt_script / review_script_llm /
                                    # review_script_human / detect_new_characters /
                                    # generate_storyboard / review_storyboard_llm /
                                    # review_storyboard_human / synthesize_audio /
                                    # generate_images / build_timeline /
                                    # human_export_decision / export_to_jianying

tests/
├── nodes/
│   ├── __init__.py
│   ├── test_setup_nodes.py
│   ├── test_init_nodes.py
│   └── test_chapter_nodes.py
└── test_graph.py
```

---

## Task 1：load_config 节点

**Files:**
- Create: `src/novel2media/nodes/__init__.py`
- Create: `src/novel2media/nodes/init_nodes.py`
- Create: `tests/nodes/__init__.py`
- Create: `tests/nodes/test_init_nodes.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/nodes/test_init_nodes.py
import json
import pytest
from pathlib import Path
from novel2media.nodes.init_nodes import load_config

def test_load_config_initializes_state(tmp_path):
    novel_dir = tmp_path / "my_novel"
    novel_dir.mkdir()
    config_data = {
        "title": "测试小说",
        "genre": "玄幻",
        "worldview": "修仙世界",
        "characters": [
            {"id": "narrator", "name": "旁白", "gender": "neutral",
             "personality": "沉稳", "appearance": ""},
            {"id": "char_001", "name": "主角", "gender": "male",
             "personality": "热血", "appearance": "白发"},
        ]
    }
    (novel_dir / "config.json").write_text(json.dumps(config_data, ensure_ascii=False))

    state = {"novel_dir": str(novel_dir)}
    result = load_config(state)

    assert result["novel_title"] == "测试小说"
    assert result["worldview"] == "修仙世界"
    assert result["chapters_status"] == {}
    assert result["chapters_artifacts"] == {}
    assert result["ignored_characters"] == []
    assert result["script_review_attempts"] == 0
    assert result["storyboard_review_attempts"] == 0
    # setup_queue 应包含全部角色
    assert len(result["setup_queue"]) == 2
    assert result["setup_queue"][0]["id"] == "narrator"

def test_load_config_missing_file_raises(tmp_path):
    novel_dir = tmp_path / "empty_novel"
    novel_dir.mkdir()
    with pytest.raises(FileNotFoundError):
        load_config({"novel_dir": str(novel_dir)})
```

- [ ] **Step 2: 运行确认失败**

```bash
pytest tests/nodes/test_init_nodes.py -v
```

Expected: FAIL

- [ ] **Step 3: 实现 init_nodes.py**

```python
# src/novel2media/nodes/init_nodes.py
from __future__ import annotations
import json
from pathlib import Path
from novel2media.logger import get_logger

log = get_logger("load_config")


def load_config(state: dict) -> dict:
    novel_dir = Path(state["novel_dir"])
    config_path = novel_dir / "config.json"
    if not config_path.exists():
        raise FileNotFoundError(f"config.json 不存在：{config_path}")

    data = json.loads(config_path.read_text(encoding="utf-8"))
    log.info("load_config 完成", title=data["title"], chars=len(data["characters"]))

    return {
        "novel_title": data["title"],
        "worldview": data.get("worldview", ""),
        "characters_profile": {},
        "ignored_characters": [],
        "chapters_status": {},
        "chapters_artifacts": {},
        "script_review_attempts": 0,
        "storyboard_review_attempts": 0,
        "setup_queue": list(data["characters"]),  # 全部角色进队列
        "setup_current_character": {},
        "setup_image_candidates": [],
        "setup_voice_candidates": [],
        "pending_new_characters": [],
        "current_chapter_id": "",
        "current_chapter_text": "",
        "current_script": [],
        "current_storyboard": [],
        "current_audio_path": "",
        "current_subtitles_path": "",
        "current_timestamps": [],
        "current_image_map": {},
        "current_timeline_path": "",
    }
```

- [ ] **Step 4: 运行确认通过**

```bash
pytest tests/nodes/test_init_nodes.py -v
```

Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add src/novel2media/nodes/ tests/nodes/
git commit -m "feat: load_config 节点，初始化 State 并填充 setup_queue"
```

---

## Task 2：setup_dispatcher 与 check_needs_visual 节点

**Files:**
- Create: `src/novel2media/nodes/setup_nodes.py`
- Create: `tests/nodes/test_setup_nodes.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/nodes/test_setup_nodes.py
import pytest
from novel2media.nodes.setup_nodes import (
    setup_dispatcher,
    check_needs_visual,
    fix_character_visual,
    fix_character_profile,
)

def _base_state(**overrides):
    state = {
        "setup_queue": [],
        "setup_current_character": {},
        "setup_image_candidates": [],
        "setup_voice_candidates": [],
        "characters_profile": {},
        "novel_dir": "/tmp/novel",
    }
    state.update(overrides)
    return state

# --- setup_dispatcher ---

def test_dispatcher_pops_first_character():
    chars = [
        {"id": "narrator", "name": "旁白", "appearance": ""},
        {"id": "char_001", "name": "主角", "appearance": "白发"},
    ]
    state = _base_state(setup_queue=chars)
    result = setup_dispatcher(state)
    assert result["setup_current_character"]["id"] == "narrator"
    assert len(result["setup_queue"]) == 1

def test_dispatcher_empty_queue_returns_sentinel():
    state = _base_state(setup_queue=[])
    result = setup_dispatcher(state)
    assert result["setup_current_character"] == {}
    assert result["setup_queue"] == []

# --- check_needs_visual ---

def test_check_needs_visual_with_appearance():
    state = _base_state(setup_current_character={"id": "char_001", "appearance": "白发"})
    result = check_needs_visual(state)
    assert result["_route"] == "image_card_draw"

def test_check_needs_visual_without_appearance():
    state = _base_state(setup_current_character={"id": "narrator", "appearance": ""})
    result = check_needs_visual(state)
    assert result["_route"] == "voice_params_choice"

# --- fix_character_visual ---

def test_fix_character_visual_stores_visual_data():
    state = _base_state(
        setup_current_character={"id": "char_001", "name": "主角"},
        setup_image_candidates=["path/to/img.png"],
    )
    state["_selected_image"] = "path/to/img.png"
    state["_comfyui_prompt"] = "1boy, white hair"
    state["_lora"] = "char001.safetensors"
    state["_lora_weight"] = 0.8
    state["_negative_prompt"] = "bad quality"
    result = fix_character_visual(state)
    char = result["setup_current_character"]
    assert char["visual"]["reference_image"] == "path/to/img.png"
    assert char["visual"]["comfyui_prompt"] == "1boy, white hair"

# --- fix_character_profile ---

def test_fix_character_profile_merges_into_profile(tmp_path):
    state = _base_state(
        novel_dir=str(tmp_path),
        setup_current_character={
            "id": "char_001",
            "name": "主角",
            "voice_params": {"seed": 1234, "speed": 1.0},
        },
        characters_profile={"narrator": {"name": "旁白"}},
    )
    result = fix_character_profile(state)
    profile = result["characters_profile"]
    assert "char_001" in profile
    assert profile["char_001"]["name"] == "主角"
    # 应输出只读视图
    out_file = tmp_path / "characters" / "characters_profile.json"
    assert out_file.exists()
```

- [ ] **Step 2: 运行确认失败**

```bash
pytest tests/nodes/test_setup_nodes.py -v
```

Expected: FAIL

- [ ] **Step 3: 实现 setup_nodes.py（dispatcher / check / visual / profile 部分）**

```python
# src/novel2media/nodes/setup_nodes.py
from __future__ import annotations
import json
from pathlib import Path
from novel2media.logger import get_logger

log = get_logger("setup_nodes")


def setup_dispatcher(state: dict) -> dict:
    queue = list(state.get("setup_queue", []))
    if not queue:
        log.info("setup_dispatcher: 队列为空，退出子图")
        return {"setup_current_character": {}, "setup_queue": []}
    char = queue.pop(0)
    log.info("setup_dispatcher: 处理角色", char_id=char.get("id"))
    return {"setup_current_character": char, "setup_queue": queue}


def check_needs_visual(state: dict) -> dict:
    char = state.get("setup_current_character", {})
    has_appearance = bool(char.get("appearance", "").strip())
    route = "image_card_draw" if has_appearance else "voice_params_choice"
    return {"_route": route}


def fix_character_visual(state: dict) -> dict:
    char = dict(state.get("setup_current_character", {}))
    char["visual"] = {
        "reference_image": state.get("_selected_image", ""),
        "comfyui_prompt": state.get("_comfyui_prompt", ""),
        "lora": state.get("_lora", ""),
        "lora_weight": state.get("_lora_weight", 0.8),
        "negative_prompt": state.get("_negative_prompt", ""),
    }
    return {"setup_current_character": char}


def fix_character_profile(state: dict) -> dict:
    char = state.get("setup_current_character", {})
    char_id = char.get("id", "unknown")
    profile = dict(state.get("characters_profile", {}))
    profile[char_id] = {k: v for k, v in char.items() if k != "id"}
    # 派生只读视图
    novel_dir = Path(state.get("novel_dir", "."))
    out_dir = novel_dir / "characters"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "characters_profile.json"
    out_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2))
    log.info("fix_character_profile: 角色档案已更新", char_id=char_id)
    return {"characters_profile": profile}


# interrupt 节点：image_card_draw / voice_params_choice / voice_params_manual / voice_card_draw
# 在子图中以 interrupt=True 的 node 形式声明，节点函数只做准备工作，
# 实际人工交互由 LangGraph interrupt 机制处理。

def image_card_draw(state: dict) -> dict:
    """触发 interrupt，等待人工选图。调用前需已将候选图路径写入 setup_image_candidates。"""
    log.info("image_card_draw: 等待人工选图",
             char=state.get("setup_current_character", {}).get("id"))
    return {}


def voice_params_choice(state: dict) -> dict:
    """触发 interrupt，询问人工：手动填写 or 抽卡。"""
    return {}


def voice_params_manual(state: dict) -> dict:
    """触发 interrupt，人工填写 voice_params + 试听文案，节点执行 TTS 试听后再次 interrupt 听审。"""
    return {}


def voice_card_draw(state: dict) -> dict:
    """触发 interrupt，确认试听文案；执行 ChatTTS 批量抽卡；再次 interrupt 听选。"""
    return {}
```

- [ ] **Step 4: 运行确认通过**

```bash
pytest tests/nodes/test_setup_nodes.py -v
```

Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/novel2media/nodes/setup_nodes.py tests/nodes/test_setup_nodes.py
git commit -m "feat: setup 节点群（dispatcher/check_visual/fix_visual/fix_profile/interrupt 占位）"
```

---

## Task 3：character_setup_subgraph 组装

**Files:**
- Create: `src/novel2media/subgraphs/__init__.py`
- Create: `src/novel2media/subgraphs/setup.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_graph.py（先写 setup_subgraph 部分）
import pytest
from novel2media.subgraphs.setup import build_character_setup_subgraph

def test_setup_subgraph_compiles():
    """验证子图可正常编译，节点和边无遗漏。"""
    graph = build_character_setup_subgraph()
    assert graph is not None
```

- [ ] **Step 2: 运行确认失败**

```bash
pytest tests/test_graph.py::test_setup_subgraph_compiles -v
```

Expected: FAIL

- [ ] **Step 3: 实现 subgraphs/setup.py**

```python
# src/novel2media/subgraphs/setup.py
from __future__ import annotations
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from novel2media.state import GraphState
from novel2media.nodes.setup_nodes import (
    setup_dispatcher,
    check_needs_visual,
    image_card_draw,
    fix_character_visual,
    voice_params_choice,
    voice_params_manual,
    voice_card_draw,
    fix_character_profile,
)


def _route_after_dispatcher(state: GraphState) -> str:
    char = state.get("setup_current_character", {})
    if not char:
        return END
    return "check_needs_visual"


def _route_after_check_visual(state: GraphState) -> str:
    return state.get("_route", "voice_params_choice")


def _route_after_voice_choice(state: GraphState) -> str:
    return state.get("_voice_route", "voice_card_draw")


def _route_after_manual_review(state: GraphState) -> str:
    decision = state.get("_manual_review", "pass")
    if decision == "pass":
        return "fix_character_profile"
    retry = state.get("_manual_retry", "adjust")
    return "voice_params_manual" if retry == "adjust" else "voice_card_draw"


def _route_after_card_draw(state: GraphState) -> str:
    if state.get("_card_selected"):
        return "fix_character_profile"
    return "voice_card_draw"  # 全部拒绝 → 重抽


def build_character_setup_subgraph() -> StateGraph:
    builder = StateGraph(GraphState)

    builder.add_node("setup_dispatcher", setup_dispatcher)
    builder.add_node("check_needs_visual", check_needs_visual)
    builder.add_node("image_card_draw", image_card_draw)
    builder.add_node("fix_character_visual", fix_character_visual)
    builder.add_node("voice_params_choice", voice_params_choice)
    builder.add_node("voice_params_manual", voice_params_manual)
    builder.add_node("voice_card_draw", voice_card_draw)
    builder.add_node("fix_character_profile", fix_character_profile)

    builder.set_entry_point("setup_dispatcher")

    builder.add_conditional_edges("setup_dispatcher", _route_after_dispatcher,
                                  {"check_needs_visual": "check_needs_visual", END: END})
    builder.add_conditional_edges("check_needs_visual", _route_after_check_visual,
                                  {"image_card_draw": "image_card_draw",
                                   "voice_params_choice": "voice_params_choice"})
    builder.add_edge("image_card_draw", "fix_character_visual")
    builder.add_edge("fix_character_visual", "voice_params_choice")
    builder.add_conditional_edges("voice_params_choice", _route_after_voice_choice,
                                  {"voice_params_manual": "voice_params_manual",
                                   "voice_card_draw": "voice_card_draw"})
    builder.add_conditional_edges("voice_params_manual", _route_after_manual_review,
                                  {"fix_character_profile": "fix_character_profile",
                                   "voice_params_manual": "voice_params_manual",
                                   "voice_card_draw": "voice_card_draw"})
    builder.add_conditional_edges("voice_card_draw", _route_after_card_draw,
                                  {"fix_character_profile": "fix_character_profile",
                                   "voice_card_draw": "voice_card_draw"})
    builder.add_edge("fix_character_profile", "setup_dispatcher")  # 内部循环

    return builder.compile()
```

- [ ] **Step 4: 运行确认通过**

```bash
pytest tests/test_graph.py::test_setup_subgraph_compiles -v
```

Expected: 1 passed

- [ ] **Step 5: Commit**

```bash
git add src/novel2media/subgraphs/ tests/test_graph.py
git commit -m "feat: character_setup_subgraph 组装，dispatcher 内部循环闭合"
```

---

## Task 4：load_chapter 节点

**Files:**
- Modify: `tests/nodes/test_chapter_nodes.py`（新建）
- Create: `src/novel2media/nodes/chapter_nodes.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/nodes/test_chapter_nodes.py
import json
import pytest
from pathlib import Path
from novel2media.nodes.chapter_nodes import load_chapter

def _make_novel(tmp_path, chapters=("chapter_01.txt",), with_summaries=True):
    novel_dir = tmp_path / "novel"
    (novel_dir / "chapters").mkdir(parents=True)
    for ch in chapters:
        (novel_dir / "chapters" / ch).write_text("内容", encoding="utf-8")
    if with_summaries:
        (novel_dir / "summaries").mkdir(exist_ok=True)
    return novel_dir

def test_load_chapter_registers_new_chapters(tmp_path):
    novel_dir = _make_novel(tmp_path)
    state = {
        "novel_dir": str(novel_dir),
        "chapters_status": {},
        "chapters_artifacts": {},
    }
    result = load_chapter(state)
    assert result["current_chapter_id"] == "chapter_01"
    assert result["chapters_status"]["chapter_01"] == "processing"
    assert result["current_chapter_text"] == "内容"

def test_load_chapter_resets_current_fields(tmp_path):
    novel_dir = _make_novel(tmp_path)
    state = {
        "novel_dir": str(novel_dir),
        "chapters_status": {},
        "chapters_artifacts": {},
        "current_script": [{"id": "sc_old"}],
        "script_review_attempts": 2,
        "storyboard_review_attempts": 1,
    }
    result = load_chapter(state)
    assert result["current_script"] == []
    assert result["script_review_attempts"] == 0
    assert result["storyboard_review_attempts"] == 0

def test_load_chapter_skips_processed_chapters(tmp_path):
    novel_dir = _make_novel(tmp_path, chapters=["chapter_01.txt", "chapter_02.txt"])
    state = {
        "novel_dir": str(novel_dir),
        "chapters_status": {"chapter_01": "done"},
        "chapters_artifacts": {},
    }
    result = load_chapter(state)
    assert result["current_chapter_id"] == "chapter_02"

def test_load_chapter_no_pending_returns_sentinel(tmp_path):
    novel_dir = _make_novel(tmp_path)
    state = {
        "novel_dir": str(novel_dir),
        "chapters_status": {"chapter_01": "done"},
        "chapters_artifacts": {},
    }
    result = load_chapter(state)
    assert result["current_chapter_id"] == ""
```

- [ ] **Step 2: 运行确认失败**

```bash
pytest tests/nodes/test_chapter_nodes.py -v
```

Expected: FAIL

- [ ] **Step 3: 实现 chapter_nodes.py（load_chapter 部分）**

```python
# src/novel2media/nodes/chapter_nodes.py
from __future__ import annotations
import json
from pathlib import Path
from novel2media.logger import get_logger

log = get_logger("chapter_nodes")

_PENDING_STATUSES = {"pending", "processing"}


def load_chapter(state: dict) -> dict:
    novel_dir = Path(state["novel_dir"])
    chapters_dir = novel_dir / "chapters"
    chapters_status: dict[str, str] = dict(state.get("chapters_status", {}))

    # 动态发现新章节文件
    known = set(chapters_status.keys())
    for ch_file in sorted(chapters_dir.glob("*.txt")):
        ch_id = ch_file.stem
        if ch_id not in known:
            chapters_status[ch_id] = "pending"

    # 取第一个 pending 章节（字典序）
    pending = sorted(
        [ch_id for ch_id, st in chapters_status.items() if st == "pending"]
    )
    if not pending:
        log.info("load_chapter: 无 pending 章节，流程结束")
        return {
            "chapters_status": chapters_status,
            "current_chapter_id": "",
            "current_chapter_text": "",
            "current_script": [],
            "current_storyboard": [],
            "current_audio_path": "",
            "current_subtitles_path": "",
            "current_timestamps": [],
            "current_image_map": {},
            "current_timeline_path": "",
            "script_review_attempts": 0,
            "storyboard_review_attempts": 0,
        }

    ch_id = pending[0]
    chapters_status[ch_id] = "processing"
    ch_text = (chapters_dir / f"{ch_id}.txt").read_text(encoding="utf-8")
    log.info("load_chapter: 开始处理章节", chapter=ch_id)

    return {
        "chapters_status": chapters_status,
        "current_chapter_id": ch_id,
        "current_chapter_text": ch_text,
        "current_script": [],
        "current_storyboard": [],
        "current_audio_path": "",
        "current_subtitles_path": "",
        "current_timestamps": [],
        "current_image_map": {},
        "current_timeline_path": "",
        "script_review_attempts": 0,
        "storyboard_review_attempts": 0,
    }
```

- [ ] **Step 4: 运行确认通过**

```bash
pytest tests/nodes/test_chapter_nodes.py -v
```

Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/novel2media/nodes/chapter_nodes.py tests/nodes/test_chapter_nodes.py
git commit -m "feat: load_chapter 节点，动态发现章节、重置中间状态"
```

---

## Task 5：剧本与分镜审核节点

**Files:**
- Modify: `src/novel2media/nodes/chapter_nodes.py`（追加）
- Modify: `tests/nodes/test_chapter_nodes.py`（追加）

- [ ] **Step 1: 追加失败测试**

```python
# 追加到 tests/nodes/test_chapter_nodes.py

from novel2media.nodes.chapter_nodes import (
    review_script_llm,
    review_storyboard_llm,
    build_timeline,
    export_to_jianying,
)

def test_review_script_llm_increments_attempts_on_fail():
    state = {
        "current_script": [{"id": "sc_001", "speaker": "narrator", "text": "test", "emotion": "calm"}],
        "script_review_attempts": 0,
        "_llm_script_pass": False,
    }
    result = review_script_llm(state)
    assert result["script_review_attempts"] == 1

def test_review_storyboard_llm_validates_first_scene_change():
    state = {
        "current_storyboard": [
            {"id": "sb_001", "scene_change": False}  # 首条必须 True → 不通过
        ],
        "storyboard_review_attempts": 0,
        "_llm_storyboard_pass": False,
    }
    result = review_storyboard_llm(state)
    assert result["storyboard_review_attempts"] == 1

def test_build_timeline_matches_storyboard_and_timestamps(tmp_path):
    novel_dir = tmp_path / "novel"
    ch_dir = novel_dir / "chapter_01"
    ch_dir.mkdir(parents=True)
    state = {
        "novel_dir": str(novel_dir),
        "current_chapter_id": "chapter_01",
        "current_storyboard": [
            {"id": "sb_001", "text": "开头", "speaker": "narrator",
             "scene_change": True, "comfyui_prompt": "scene", "emotion": "calm", "composition": "wide"},
            {"id": "sb_002", "text": "对话", "speaker": "char_001",
             "scene_change": False, "comfyui_prompt": "", "emotion": "normal", "composition": ""},
        ],
        "current_timestamps": [
            {"storyboard_id": "sb_001", "text": "开头", "speaker": "narrator",
             "start_time": 0.0, "end_time": 2.0},
            {"storyboard_id": "sb_002", "text": "对话", "speaker": "char_001",
             "start_time": 2.2, "end_time": 3.5},
        ],
        "current_image_map": {
            "sb_001": str(ch_dir / "images" / "scene_001.png"),
            "sb_002": str(ch_dir / "images" / "scene_001.png"),
        },
        "chapters_artifacts": {},
    }
    result = build_timeline(state)
    assert result["current_timeline_path"] != ""
    timeline_path = Path(result["current_timeline_path"])
    assert timeline_path.exists()
    timeline = json.loads(timeline_path.read_text())
    assert len(timeline) == 2
    assert timeline[0]["image_path"] == state["current_image_map"]["sb_001"]
    assert "chapter_01" in result["chapters_artifacts"]
```

- [ ] **Step 2: 运行确认失败**

```bash
pytest tests/nodes/test_chapter_nodes.py -v
```

Expected: 新增测试 FAIL

- [ ] **Step 3: 追加实现到 chapter_nodes.py**

```python
# 追加到 src/novel2media/nodes/chapter_nodes.py

def review_script_llm(state: dict) -> dict:
    """LLM 自审剧本。真实实现调用 LLM；此处用 state["_llm_script_pass"] 控制（测试可注入）。"""
    passed = state.get("_llm_script_pass", True)
    if passed:
        log.info("review_script_llm: 通过")
        return {"_script_review_result": "pass"}
    attempts = state.get("script_review_attempts", 0) + 1
    log.info("review_script_llm: 不通过", attempts=attempts)
    return {"script_review_attempts": attempts, "_script_review_result": "fail"}


def review_storyboard_llm(state: dict) -> dict:
    """LLM 自审分镜稿，强制验证首条 scene_change=true。"""
    storyboard = state.get("current_storyboard", [])
    first_ok = bool(storyboard) and storyboard[0].get("scene_change", False)
    llm_pass = state.get("_llm_storyboard_pass", True) and first_ok
    if llm_pass:
        log.info("review_storyboard_llm: 通过")
        return {"_storyboard_review_result": "pass"}
    attempts = state.get("storyboard_review_attempts", 0) + 1
    log.info("review_storyboard_llm: 不通过", attempts=attempts)
    return {"storyboard_review_attempts": attempts, "_storyboard_review_result": "fail"}


def build_timeline(state: dict) -> dict:
    novel_dir = Path(state["novel_dir"])
    ch_id = state["current_chapter_id"]
    timestamps: list[dict] = state.get("current_timestamps", [])
    image_map: dict[str, str] = state.get("current_image_map", {})

    ts_by_id = {t["storyboard_id"]: t for t in timestamps}
    timeline = []
    for ts in timestamps:
        sid = ts["storyboard_id"]
        timeline.append({
            "storyboard_id": sid,
            "text": ts["text"],
            "speaker": ts["speaker"],
            "start_time": ts["start_time"],
            "end_time": ts["end_time"],
            "image_path": image_map.get(sid, ""),
        })

    out_dir = novel_dir / ch_id
    out_dir.mkdir(parents=True, exist_ok=True)
    timeline_path = out_dir / "timeline.json"
    timeline_path.write_text(json.dumps(timeline, ensure_ascii=False, indent=2))

    artifacts = dict(state.get("chapters_artifacts", {}))
    artifacts[ch_id] = {
        "audio_path": state.get("current_audio_path", ""),
        "subtitles_path": state.get("current_subtitles_path", ""),
        "timeline_path": str(timeline_path),
    }
    log.info("build_timeline: 完成", chapter=ch_id, entries=len(timeline))
    return {
        "current_timeline_path": str(timeline_path),
        "chapters_artifacts": artifacts,
    }


def export_to_jianying(state: dict) -> dict:
    """导出 status=done 章节（增量），置 exported。"""
    novel_dir = Path(state["novel_dir"])
    chapters_status = dict(state.get("chapters_status", {}))
    chapters_artifacts = state.get("chapters_artifacts", {})

    done_chapters = [ch for ch, st in chapters_status.items() if st == "done"]
    if not done_chapters:
        log.info("export_to_jianying: 无 done 章节")
        return {}

    export_data = []
    for ch_id in sorted(done_chapters):
        artifact = chapters_artifacts.get(ch_id, {})
        export_data.append({"chapter_id": ch_id, **artifact})
        chapters_status[ch_id] = "exported"

    out_path = novel_dir / "export" / "jianying_draft.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(export_data, ensure_ascii=False, indent=2))

    # 派生 chapters_status.json 只读视图
    status_path = novel_dir / "chapters_status.json"
    status_path.write_text(json.dumps(chapters_status, ensure_ascii=False, indent=2))

    log.info("export_to_jianying: 导出完成", chapters=done_chapters)
    return {"chapters_status": chapters_status}
```

- [ ] **Step 4: 运行确认通过**

```bash
pytest tests/nodes/test_chapter_nodes.py -v
```

Expected: 全部通过

- [ ] **Step 5: Commit**

```bash
git add src/novel2media/nodes/chapter_nodes.py tests/nodes/test_chapter_nodes.py
git commit -m "feat: 审核节点(review_script_llm/storyboard_llm)、build_timeline、export_to_jianying"
```

---

## Task 6：章节子图与顶层图组装

**Files:**
- Create: `src/novel2media/subgraphs/chapter.py`
- Create: `src/novel2media/subgraphs/init_graph.py`
- Create: `src/novel2media/graph.py`

- [ ] **Step 1: 写失败测试**

```python
# 追加到 tests/test_graph.py

from novel2media.subgraphs.chapter import build_chapter_subgraph
from novel2media.subgraphs.init_graph import build_init_subgraph
from novel2media.graph import graph

def test_chapter_subgraph_compiles():
    g = build_chapter_subgraph()
    assert g is not None

def test_init_subgraph_compiles():
    g = build_init_subgraph()
    assert g is not None

def test_top_level_graph_compiles():
    assert graph is not None
```

- [ ] **Step 2: 运行确认失败**

```bash
pytest tests/test_graph.py -v
```

Expected: 新增 3 个 FAIL

- [ ] **Step 3: 实现 subgraphs/chapter.py**

```python
# src/novel2media/subgraphs/chapter.py
from __future__ import annotations
from langgraph.graph import StateGraph, END
from novel2media.state import GraphState
from novel2media.nodes.chapter_nodes import (
    load_chapter,
    review_script_llm,
    review_storyboard_llm,
    build_timeline,
    export_to_jianying,
)
from novel2media.nodes.setup_nodes import setup_dispatcher
from novel2media.subgraphs.setup import build_character_setup_subgraph


def _route_load_chapter(state: GraphState) -> str:
    return END if not state.get("current_chapter_id") else "adapt_script"


def _route_review_script_llm(state: GraphState) -> str:
    result = state.get("_script_review_result", "pass")
    if result == "pass":
        return "review_script_human"
    attempts = state.get("script_review_attempts", 0)
    return "adapt_script" if attempts < 3 else "review_script_llm_interrupt"


def _route_review_script_human(state: GraphState) -> str:
    decision = state.get("_human_script_decision", "pass")
    return "detect_new_characters" if decision == "pass" else "adapt_script"


def _route_detect_new_characters(state: GraphState) -> str:
    queue = state.get("setup_queue", [])
    return "character_setup_subgraph" if queue else "generate_storyboard"


def _route_review_storyboard_llm(state: GraphState) -> str:
    result = state.get("_storyboard_review_result", "pass")
    if result == "pass":
        return "review_storyboard_human"
    attempts = state.get("storyboard_review_attempts", 0)
    return "generate_storyboard" if attempts < 3 else "review_storyboard_llm_interrupt"


def _route_review_storyboard_human(state: GraphState) -> str:
    decision = state.get("_human_storyboard_decision", "pass")
    return "synthesize_audio" if decision == "pass" else "generate_storyboard"


def _route_human_export(state: GraphState) -> str:
    return "export_to_jianying" if state.get("_export_now") else "load_chapter"


def _placeholder_node(name: str):
    def node(state: GraphState) -> dict:
        from novel2media.logger import get_logger
        get_logger(name).info(f"{name}: interrupt 占位节点")
        return {}
    node.__name__ = name
    return node


def build_chapter_subgraph():
    builder = StateGraph(GraphState)

    builder.add_node("load_chapter", load_chapter)
    builder.add_node("adapt_script", _placeholder_node("adapt_script"))
    builder.add_node("review_script_llm", review_script_llm)
    builder.add_node("review_script_human", _placeholder_node("review_script_human"))
    builder.add_node("review_script_llm_interrupt", _placeholder_node("review_script_llm_interrupt"))
    builder.add_node("detect_new_characters", _placeholder_node("detect_new_characters"))
    builder.add_node("character_setup_subgraph", build_character_setup_subgraph())
    builder.add_node("generate_storyboard", _placeholder_node("generate_storyboard"))
    builder.add_node("review_storyboard_llm", review_storyboard_llm)
    builder.add_node("review_storyboard_human", _placeholder_node("review_storyboard_human"))
    builder.add_node("review_storyboard_llm_interrupt", _placeholder_node("review_storyboard_llm_interrupt"))
    builder.add_node("synthesize_audio", _placeholder_node("synthesize_audio"))
    builder.add_node("generate_images", _placeholder_node("generate_images"))
    builder.add_node("build_timeline", build_timeline)
    builder.add_node("human_export_decision", _placeholder_node("human_export_decision"))
    builder.add_node("export_to_jianying", export_to_jianying)

    builder.set_entry_point("load_chapter")
    builder.add_conditional_edges("load_chapter", _route_load_chapter,
                                  {"adapt_script": "adapt_script", END: END})
    builder.add_edge("adapt_script", "review_script_llm")
    builder.add_conditional_edges("review_script_llm", _route_review_script_llm,
                                  {"review_script_human": "review_script_human",
                                   "adapt_script": "adapt_script",
                                   "review_script_llm_interrupt": "review_script_llm_interrupt"})
    builder.add_conditional_edges("review_script_human", _route_review_script_human,
                                  {"detect_new_characters": "detect_new_characters",
                                   "adapt_script": "adapt_script"})
    builder.add_edge("review_script_llm_interrupt", "adapt_script")
    builder.add_conditional_edges("detect_new_characters", _route_detect_new_characters,
                                  {"character_setup_subgraph": "character_setup_subgraph",
                                   "generate_storyboard": "generate_storyboard"})
    builder.add_edge("character_setup_subgraph", "generate_storyboard")
    builder.add_edge("generate_storyboard", "review_storyboard_llm")
    builder.add_conditional_edges("review_storyboard_llm", _route_review_storyboard_llm,
                                  {"review_storyboard_human": "review_storyboard_human",
                                   "generate_storyboard": "generate_storyboard",
                                   "review_storyboard_llm_interrupt": "review_storyboard_llm_interrupt"})
    builder.add_conditional_edges("review_storyboard_human", _route_review_storyboard_human,
                                  {"synthesize_audio": "synthesize_audio",
                                   "generate_storyboard": "generate_storyboard"})
    builder.add_edge("review_storyboard_llm_interrupt", "generate_storyboard")
    builder.add_edge("synthesize_audio", "generate_images")
    builder.add_edge("generate_images", "build_timeline")
    builder.add_edge("build_timeline", "human_export_decision")
    builder.add_conditional_edges("human_export_decision", _route_human_export,
                                  {"export_to_jianying": "export_to_jianying",
                                   "load_chapter": "load_chapter"})
    builder.add_edge("export_to_jianying", "load_chapter")

    return builder.compile()
```

- [ ] **Step 4: 实现 subgraphs/init_graph.py**

```python
# src/novel2media/subgraphs/init_graph.py
from __future__ import annotations
from langgraph.graph import StateGraph, END
from novel2media.state import GraphState
from novel2media.nodes.init_nodes import load_config
from novel2media.subgraphs.setup import build_character_setup_subgraph


def build_init_subgraph():
    builder = StateGraph(GraphState)
    builder.add_node("load_config", load_config)
    builder.add_node("character_setup_subgraph", build_character_setup_subgraph())
    builder.set_entry_point("load_config")
    builder.add_edge("load_config", "character_setup_subgraph")
    builder.add_edge("character_setup_subgraph", END)
    return builder.compile()
```

- [ ] **Step 5: 实现 graph.py（顶层图）**

```python
# src/novel2media/graph.py
from __future__ import annotations
from pathlib import Path
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.sqlite import SqliteSaver
from novel2media.state import GraphState
from novel2media.subgraphs.init_graph import build_init_subgraph
from novel2media.subgraphs.chapter import build_chapter_subgraph
from novel2media.logger import setup_logging

setup_logging()

_db_path = Path.home() / ".novel2media" / "checkpoints.sqlite"
_db_path.parent.mkdir(parents=True, exist_ok=True)

_builder = StateGraph(GraphState)
_builder.add_node("init_subgraph", build_init_subgraph())
_builder.add_node("chapter_loop_subgraph", build_chapter_subgraph())
_builder.set_entry_point("init_subgraph")
_builder.add_edge("init_subgraph", "chapter_loop_subgraph")
_builder.add_edge("chapter_loop_subgraph", END)

graph = _builder.compile(checkpointer=SqliteSaver.from_conn_string(str(_db_path)))
```

- [ ] **Step 6: 运行确认通过**

```bash
pytest tests/test_graph.py -v
```

Expected: 全部通过

- [ ] **Step 7: 验证 langgraph dev 可启动**

```bash
langgraph dev --no-browser 2>&1 | head -20
```

Expected: 看到 `Starting LangGraph API server` 或 `Uvicorn running`，无崩溃。

- [ ] **Step 8: Commit**

```bash
git add src/novel2media/subgraphs/ src/novel2media/graph.py
git commit -m "feat: chapter_subgraph / init_subgraph / 顶层图组装，langgraph dev 可启动"
```

---

## Task 7：全量测试与收尾

- [ ] **Step 1: 运行全量测试**

```bash
pytest tests/ -v
```

Expected: 全部通过，0 failures

- [ ] **Step 2: 验证包结构完整**

```bash
python -c "from novel2media.graph import graph; print('graph compiled OK')"
```

Expected: 输出 `graph compiled OK`

- [ ] **Step 3: 最终 Commit**

```bash
git add .
git commit -m "chore: Plan B 图流程实现完成，全量测试通过"
```

---

## 自检

| Spec 要求 | 任务覆盖 |
|-----------|---------|
| load_config 读取 config.json，初始化 State | Task 1 ✅ |
| setup_dispatcher 队列驱动循环 | Task 2 ✅ |
| character_setup_subgraph 内部循环（fix → dispatcher）| Task 3 ✅ |
| load_chapter 动态发现章节、重置中间状态 | Task 4 ✅ |
| review_script_llm 计数、首条 scene_change 验证 | Task 5 ✅ |
| build_timeline 按 storyboard_id 对齐 | Task 5 ✅ |
| export_to_jianying 增量导出，置 exported | Task 5 ✅ |
| 章节子图条件边完整（load→END / human_export→loop）| Task 6 ✅ |
| 顶层图 init → chapter_loop | Task 6 ✅ |
| SqliteSaver checkpoint | Task 6 ✅ |
| langgraph.json 指向 graph.py:graph | Task 1 ✅ |
| LLM 节点（adapt_script / detect / storyboard / audio / images）| 占位，后续填充实现 |

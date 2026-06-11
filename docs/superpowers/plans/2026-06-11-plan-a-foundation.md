# 小说转多媒体系统 Plan A：基础层实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 搭建项目骨架、State 定义、外部服务客户端（ChatTTS / ComfyUI）、音频处理工具，为 Plan B 的图流程实现打好基础。

**Architecture:** Python 包结构，`src/novel2media/` 为主包。State 定义集中在 `state.py`，外部服务各自独立客户端模块，音频处理独立工具模块。所有外部服务通过 `config/services.json` 统一配置，不硬编码。

**Tech Stack:** Python 3.11+, LangGraph 0.2+, langgraph-checkpoint-sqlite, structlog, httpx, pydub, ffmpeg（系统依赖）

---

## 文件结构

```
text-image/
├── pyproject.toml                          # 项目依赖
├── langgraph.json                          # LangGraph Studio 配置
├── config/
│   └── services.json                       # 外部服务配置（含占位 IP）
├── src/
│   └── novel2media/
│       ├── __init__.py
│       ├── state.py                        # GraphState / ChapterArtifacts / ChapterStatus
│       ├── config.py                       # ServicesConfig 加载器
│       ├── logger.py                       # structlog 初始化
│       ├── clients/
│       │   ├── __init__.py
│       │   ├── tts.py                      # ChatTTS HTTP 客户端
│       │   └── comfyui.py                  # ComfyUI HTTP 客户端
│       └── audio/
│           ├── __init__.py
│           └── pipeline.py                 # 音频拼接 + 归一化
└── tests/
    ├── test_state.py
    ├── test_config.py
    ├── clients/
    │   ├── test_tts.py
    │   └── test_comfyui.py
    └── audio/
        └── test_pipeline.py
```

---

## Task 1：项目骨架与依赖

**Files:**
- Create: `pyproject.toml`
- Create: `langgraph.json`
- Create: `config/services.json`
- Create: `src/novel2media/__init__.py`

- [ ] **Step 1: 创建 pyproject.toml**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "novel2media"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "langgraph>=0.2.0",
    "langgraph-checkpoint-sqlite>=1.0.0",
    "langchain-openai>=0.1.0",
    "langchain-anthropic>=0.1.0",
    "structlog>=24.1.0",
    "httpx>=0.27.0",
    "pydub>=0.25.1",
    "python-dotenv>=1.0.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.23.0",
    "pytest-mock>=3.12.0",
    "respx>=0.21.0",
]

[tool.hatch.build.targets.wheel]
packages = ["src/novel2media"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 2: 创建 langgraph.json**

```json
{
  "dependencies": ["."],
  "graphs": {
    "novel2media": "./src/novel2media/graph.py:graph"
  },
  "env": ".env"
}
```

- [ ] **Step 3: 创建 config/services.json**

```json
{
  "comfyui": {
    "base_url": "http://192.168.1.100:8188",
    "timeout": 120
  },
  "tts_remote": {
    "base_url": "http://192.168.1.100:9000",
    "timeout": 60
  },
  "card_draw": {
    "image_candidates": 4,
    "voice_candidates": 3
  },
  "retry": {
    "max_attempts": 3,
    "backoff_seconds": 5
  },
  "audio": {
    "silence_between_speakers_ms": 200,
    "target_loudness_lufs": -16
  },
  "llm_context": {
    "prev_chapters_for_script": 3
  },
  "default_preview_text": "春风十里，不如你。今日天气甚好，出门走走。这江山如此多娇，引无数英雄竞折腰。"
}
```

- [ ] **Step 4: 创建包入口**

```python
# src/novel2media/__init__.py
```

- [ ] **Step 5: 安装依赖**

```bash
pip install -e ".[dev]"
```

Expected: 无报错，`import novel2media` 成功。

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml langgraph.json config/services.json src/
git commit -m "feat: 项目骨架、依赖配置、langgraph.json"
```

---

## Task 2：ServicesConfig 加载器

**Files:**
- Create: `src/novel2media/config.py`
- Create: `tests/test_config.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_config.py
import json
import pytest
from pathlib import Path
from novel2media.config import ServicesConfig

def test_load_services_config(tmp_path):
    cfg_file = tmp_path / "services.json"
    cfg_file.write_text(json.dumps({
        "comfyui": {"base_url": "http://1.2.3.4:8188", "timeout": 120},
        "tts_remote": {"base_url": "http://1.2.3.4:9000", "timeout": 60},
        "card_draw": {"image_candidates": 4, "voice_candidates": 3},
        "retry": {"max_attempts": 3, "backoff_seconds": 5},
        "audio": {"silence_between_speakers_ms": 200, "target_loudness_lufs": -16},
        "llm_context": {"prev_chapters_for_script": 3},
        "default_preview_text": "test text"
    }))
    cfg = ServicesConfig.from_file(cfg_file)
    assert cfg.comfyui_url == "http://1.2.3.4:8188"
    assert cfg.tts_url == "http://1.2.3.4:9000"
    assert cfg.image_candidates == 4
    assert cfg.voice_candidates == 3
    assert cfg.retry_max == 3
    assert cfg.silence_ms == 200
    assert cfg.lufs == -16
    assert cfg.prev_chapters == 3
    assert cfg.default_preview_text == "test text"

def test_missing_config_file_raises():
    with pytest.raises(FileNotFoundError):
        ServicesConfig.from_file(Path("/nonexistent/services.json"))
```

- [ ] **Step 2: 运行确认失败**

```bash
pytest tests/test_config.py -v
```

Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: 实现 ServicesConfig**

```python
# src/novel2media/config.py
from __future__ import annotations
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ServicesConfig:
    comfyui_url: str
    comfyui_timeout: int
    tts_url: str
    tts_timeout: int
    image_candidates: int
    voice_candidates: int
    retry_max: int
    retry_backoff: float
    silence_ms: int
    lufs: int
    prev_chapters: int
    default_preview_text: str

    @classmethod
    def from_file(cls, path: Path) -> "ServicesConfig":
        if not path.exists():
            raise FileNotFoundError(f"services.json not found: {path}")
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            comfyui_url=data["comfyui"]["base_url"],
            comfyui_timeout=data["comfyui"]["timeout"],
            tts_url=data["tts_remote"]["base_url"],
            tts_timeout=data["tts_remote"]["timeout"],
            image_candidates=data["card_draw"]["image_candidates"],
            voice_candidates=data["card_draw"]["voice_candidates"],
            retry_max=data["retry"]["max_attempts"],
            retry_backoff=data["retry"]["backoff_seconds"],
            silence_ms=data["audio"]["silence_between_speakers_ms"],
            lufs=data["audio"]["target_loudness_lufs"],
            prev_chapters=data["llm_context"]["prev_chapters_for_script"],
            default_preview_text=data["default_preview_text"],
        )
```

- [ ] **Step 4: 运行确认通过**

```bash
pytest tests/test_config.py -v
```

Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add src/novel2media/config.py tests/test_config.py
git commit -m "feat: ServicesConfig 从 services.json 加载外部服务配置"
```

---

## Task 3：structlog 日志初始化

**Files:**
- Create: `src/novel2media/logger.py`
- Create: `tests/test_logger.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_logger.py
from novel2media.logger import get_logger, setup_logging

def test_get_logger_returns_bound_logger():
    setup_logging()
    log = get_logger("test_node")
    # structlog BoundLogger 有 info/error 方法
    assert hasattr(log, "info")
    assert hasattr(log, "error")

def test_logger_binds_node_name():
    setup_logging()
    log = get_logger("load_chapter")
    bound = log.bind(chapter="ch_001")
    assert hasattr(bound, "info")
```

- [ ] **Step 2: 运行确认失败**

```bash
pytest tests/test_logger.py -v
```

Expected: FAIL

- [ ] **Step 3: 实现 logger.py**

```python
# src/novel2media/logger.py
import structlog


def setup_logging() -> None:
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.stdlib.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(20),  # INFO
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(node_name: str) -> structlog.BoundLogger:
    return structlog.get_logger().bind(node=node_name)
```

- [ ] **Step 4: 运行确认通过**

```bash
pytest tests/test_logger.py -v
```

Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add src/novel2media/logger.py tests/test_logger.py
git commit -m "feat: structlog 日志初始化，get_logger 绑定节点名"
```

---

## Task 4：GraphState 与数据结构定义

**Files:**
- Create: `src/novel2media/state.py`
- Create: `tests/test_state.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_state.py
from novel2media.state import GraphState, ChapterArtifacts, ChapterStatus

def test_chapter_status_values():
    assert ChapterStatus.PENDING == "pending"
    assert ChapterStatus.PROCESSING == "processing"
    assert ChapterStatus.DONE == "done"
    assert ChapterStatus.EXPORTED == "exported"

def test_chapter_artifacts_keys():
    artifact: ChapterArtifacts = {
        "audio_path": "/output/ch1/audio.wav",
        "subtitles_path": "/output/ch1/subtitles.srt",
        "timeline_path": "/output/ch1/timeline.json",
    }
    assert artifact["audio_path"] == "/output/ch1/audio.wav"

def test_graph_state_shape():
    # 验证 GraphState 是 TypedDict，包含所有预期 key
    keys = set(GraphState.__annotations__.keys())
    required = {
        "novel_title", "novel_dir", "worldview",
        "characters_profile", "ignored_characters",
        "chapters_status", "chapters_artifacts",
        "current_chapter_id", "current_chapter_text",
        "current_script", "current_storyboard",
        "current_audio_path", "current_subtitles_path",
        "current_timestamps", "current_image_map", "current_timeline_path",
        "script_review_attempts", "storyboard_review_attempts",
        "setup_queue", "setup_current_character",
        "setup_image_candidates", "setup_voice_candidates",
        "pending_new_characters",
    }
    assert required.issubset(keys)
```

- [ ] **Step 2: 运行确认失败**

```bash
pytest tests/test_state.py -v
```

Expected: FAIL

- [ ] **Step 3: 实现 state.py**

```python
# src/novel2media/state.py
from __future__ import annotations
from enum import Enum
from typing import TypedDict


class ChapterStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    EXPORTED = "exported"


class ChapterArtifacts(TypedDict):
    audio_path: str
    subtitles_path: str
    timeline_path: str
    # image_path 已含于 timeline.json 每条记录中，此处不重复存储


class GraphState(TypedDict):
    # 全局配置
    novel_title: str
    novel_dir: str
    worldview: str

    # 角色管理
    characters_profile: dict            # 角色完整档案（唯一真相）
    ignored_characters: list[str]       # 已忽略角色名列表

    # 章节状态与产物（历史章节数据累积存储，支持跨章导出）
    chapters_status: dict[str, str]     # chapter_id → ChapterStatus
    chapters_artifacts: dict[str, ChapterArtifacts]  # chapter_id → 产物路径

    # 当前章节中间状态（load_chapter 时全部重置）
    current_chapter_id: str
    current_chapter_text: str
    current_script: list[dict]
    current_storyboard: list[dict]
    current_audio_path: str
    current_subtitles_path: str
    current_timestamps: list[dict]      # 含全局偏移后时间戳
    current_image_map: dict[str, str]   # storyboard_id → image_path（generate_images 中间结果）
    current_timeline_path: str

    # 审核重试计数器（load_chapter 统一重置）
    script_review_attempts: int
    storyboard_review_attempts: int

    # character_setup_subgraph 内部状态（子图自驱动队列循环）
    setup_queue: list[dict]             # 待设定角色队列，dispatcher 逐个弹出
    setup_current_character: dict       # 当前待处理的单个角色信息
    setup_image_candidates: list[str]   # 当前角色的候选图片路径列表
    setup_voice_candidates: list[dict]  # 当前角色的候选音色列表（seed + 样本路径）

    # detect_new_characters 中间结果
    pending_new_characters: list[dict]  # 待人工决策的新角色列表
```

- [ ] **Step 4: 运行确认通过**

```bash
pytest tests/test_state.py -v
```

Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/novel2media/state.py tests/test_state.py
git commit -m "feat: GraphState / ChapterArtifacts / ChapterStatus 数据结构定义"
```

---

## Task 5：ChatTTS HTTP 客户端

**Files:**
- Create: `src/novel2media/clients/__init__.py`
- Create: `src/novel2media/clients/tts.py`
- Create: `tests/clients/test_tts.py`
- Create: `tests/clients/__init__.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/clients/test_tts.py
import pytest
import respx
import httpx
from novel2media.clients.tts import TTSClient, TTSResult

BASE = "http://tts.local:9000"

@respx.mock
def test_synthesize_returns_audio_and_timestamps():
    mock_resp = {
        "audio": "AAAA",  # base64 placeholder
        "timestamps": [
            {"text": "你好", "start_time": 0.0, "end_time": 1.2,
             "words": [{"char": "你", "s": 0.0, "e": 0.6}]}
        ]
    }
    respx.post(f"{BASE}/tts").mock(return_value=httpx.Response(200, json=mock_resp))

    client = TTSClient(base_url=BASE, timeout=10)
    result = client.synthesize(
        text="你好",
        voice_params={"seed": 1234, "speed": 1.0, "oral": 2, "laugh": 0,
                      "break": 3, "temperature": 0.3, "top_p": 0.7, "top_k": 20}
    )
    assert isinstance(result, TTSResult)
    assert result.audio_b64 == "AAAA"
    assert result.timestamps[0]["text"] == "你好"
    assert result.timestamps[0]["end_time"] == 1.2

@respx.mock
def test_synthesize_retries_on_failure():
    respx.post(f"{BASE}/tts").mock(side_effect=[
        httpx.Response(500),
        httpx.Response(500),
        httpx.Response(200, json={"audio": "BBBB", "timestamps": []}),
    ])
    client = TTSClient(base_url=BASE, timeout=10, max_retries=3, backoff=0)
    result = client.synthesize(text="test", voice_params={"seed": 1})
    assert result.audio_b64 == "BBBB"

@respx.mock
def test_synthesize_raises_after_max_retries():
    respx.post(f"{BASE}/tts").mock(return_value=httpx.Response(500))
    client = TTSClient(base_url=BASE, timeout=10, max_retries=2, backoff=0)
    with pytest.raises(RuntimeError, match="TTS 调用失败"):
        client.synthesize(text="test", voice_params={"seed": 1})
```

- [ ] **Step 2: 运行确认失败**

```bash
pytest tests/clients/test_tts.py -v
```

Expected: FAIL

- [ ] **Step 3: 创建 `tests/clients/__init__.py`（空文件）**

- [ ] **Step 4: 实现 tts.py**

```python
# src/novel2media/clients/tts.py
from __future__ import annotations
import time
from dataclasses import dataclass
from typing import Any
import httpx
from novel2media.logger import get_logger

log = get_logger("tts_client")


@dataclass
class TTSResult:
    audio_b64: str
    timestamps: list[dict[str, Any]]


class TTSClient:
    def __init__(
        self,
        base_url: str,
        timeout: int = 60,
        max_retries: int = 3,
        backoff: float = 5.0,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout
        self._max_retries = max_retries
        self._backoff = backoff

    def synthesize(self, text: str, voice_params: dict[str, Any]) -> TTSResult:
        payload = {"text": text, **voice_params}
        for attempt in range(1, self._max_retries + 1):
            try:
                resp = httpx.post(
                    f"{self._base}/tts",
                    json=payload,
                    timeout=self._timeout,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return TTSResult(
                        audio_b64=data["audio"],
                        timestamps=data.get("timestamps", []),
                    )
                log.warning("TTS 非 200 响应", status=resp.status_code, attempt=attempt)
            except httpx.RequestError as e:
                log.warning("TTS 请求异常", error=str(e), attempt=attempt)
            if attempt < self._max_retries:
                time.sleep(self._backoff)
        raise RuntimeError(f"TTS 调用失败，已重试 {self._max_retries} 次")
```

- [ ] **Step 5: 运行确认通过**

```bash
pytest tests/clients/test_tts.py -v
```

Expected: 3 passed

- [ ] **Step 6: Commit**

```bash
git add src/novel2media/clients/ tests/clients/
git commit -m "feat: ChatTTS HTTP 客户端，含重试逻辑"
```

---

## Task 6：ComfyUI HTTP 客户端

**Files:**
- Create: `src/novel2media/clients/comfyui.py`
- Create: `tests/clients/test_comfyui.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/clients/test_comfyui.py
import pytest
import respx
import httpx
from novel2media.clients.comfyui import ComfyUIClient

BASE = "http://comfy.local:8188"

@respx.mock
def test_generate_images_returns_paths(tmp_path):
    prompt_id = "abc123"
    respx.post(f"{BASE}/prompt").mock(
        return_value=httpx.Response(200, json={"prompt_id": prompt_id})
    )
    image_bytes = b"FAKEPNG"
    respx.get(f"{BASE}/history/{prompt_id}").mock(
        return_value=httpx.Response(200, json={
            prompt_id: {
                "outputs": {
                    "9": {"images": [{"filename": "ComfyUI_00001_.png", "subfolder": "", "type": "output"}]}
                }
            }
        })
    )
    respx.get(f"{BASE}/view").mock(return_value=httpx.Response(200, content=image_bytes))

    client = ComfyUIClient(base_url=BASE, timeout=10, poll_interval=0)
    paths = client.generate(
        workflow_prompt={"positive": "a cat", "negative": ""},
        output_dir=tmp_path,
        count=1,
    )
    assert len(paths) == 1
    assert paths[0].exists()
    assert paths[0].read_bytes() == image_bytes

@respx.mock
def test_generate_raises_on_prompt_failure():
    respx.post(f"{BASE}/prompt").mock(return_value=httpx.Response(500))
    client = ComfyUIClient(base_url=BASE, timeout=10, max_retries=1, backoff=0)
    with pytest.raises(RuntimeError, match="ComfyUI prompt 提交失败"):
        from pathlib import Path
        client.generate(workflow_prompt={}, output_dir=Path("/tmp"), count=1)
```

- [ ] **Step 2: 运行确认失败**

```bash
pytest tests/clients/test_comfyui.py -v
```

Expected: FAIL

- [ ] **Step 3: 实现 comfyui.py**

```python
# src/novel2media/clients/comfyui.py
from __future__ import annotations
import time
from pathlib import Path
import httpx
from novel2media.logger import get_logger

log = get_logger("comfyui_client")


class ComfyUIClient:
    def __init__(
        self,
        base_url: str,
        timeout: int = 120,
        max_retries: int = 3,
        backoff: float = 5.0,
        poll_interval: float = 2.0,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout
        self._max_retries = max_retries
        self._backoff = backoff
        self._poll_interval = poll_interval

    def generate(
        self,
        workflow_prompt: dict,
        output_dir: Path,
        count: int,
    ) -> list[Path]:
        prompt_id = self._submit_prompt(workflow_prompt)
        images_info = self._wait_for_output(prompt_id)
        output_dir.mkdir(parents=True, exist_ok=True)
        paths: list[Path] = []
        for i, img in enumerate(images_info[:count]):
            data = self._download_image(img["filename"], img.get("subfolder", ""))
            dest = output_dir / f"candidate_{i:02d}_{img['filename']}"
            dest.write_bytes(data)
            paths.append(dest)
        return paths

    def _submit_prompt(self, prompt: dict) -> str:
        for attempt in range(1, self._max_retries + 1):
            try:
                resp = httpx.post(
                    f"{self._base}/prompt",
                    json={"prompt": prompt},
                    timeout=self._timeout,
                )
                if resp.status_code == 200:
                    return resp.json()["prompt_id"]
                log.warning("ComfyUI prompt 提交失败", status=resp.status_code, attempt=attempt)
            except httpx.RequestError as e:
                log.warning("ComfyUI 请求异常", error=str(e), attempt=attempt)
            if attempt < self._max_retries:
                time.sleep(self._backoff)
        raise RuntimeError(f"ComfyUI prompt 提交失败，已重试 {self._max_retries} 次")

    def _wait_for_output(self, prompt_id: str) -> list[dict]:
        while True:
            resp = httpx.get(f"{self._base}/history/{prompt_id}", timeout=self._timeout)
            if resp.status_code == 200:
                history = resp.json()
                if prompt_id in history:
                    outputs = history[prompt_id].get("outputs", {})
                    images: list[dict] = []
                    for node_output in outputs.values():
                        images.extend(node_output.get("images", []))
                    return images
            time.sleep(self._poll_interval)

    def _download_image(self, filename: str, subfolder: str) -> bytes:
        resp = httpx.get(
            f"{self._base}/view",
            params={"filename": filename, "subfolder": subfolder, "type": "output"},
            timeout=self._timeout,
        )
        resp.raise_for_status()
        return resp.content
```

- [ ] **Step 4: 运行确认通过**

```bash
pytest tests/clients/test_comfyui.py -v
```

Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add src/novel2media/clients/comfyui.py tests/clients/test_comfyui.py
git commit -m "feat: ComfyUI HTTP 客户端，含 prompt 提交/轮询/下载"
```

---

## Task 7：音频拼接与归一化工具

**Files:**
- Create: `src/novel2media/audio/__init__.py`
- Create: `src/novel2media/audio/pipeline.py`
- Create: `tests/audio/__init__.py`
- Create: `tests/audio/test_pipeline.py`

注意：`pydub` 需要系统安装 `ffmpeg`。测试用 mock 替代真实音频处理，避免 CI 依赖 ffmpeg。

- [ ] **Step 1: 写失败测试**

```python
# tests/audio/test_pipeline.py
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path
from novel2media.audio.pipeline import AudioPipeline, AudioSegment as Seg

def test_build_srt_single_entry():
    pipe = AudioPipeline(silence_ms=200, lufs=-16)
    entries = [
        {"storyboard_id": "sb_001", "text": "你好世界", "start_time": 0.0, "end_time": 1.5}
    ]
    srt = pipe.build_srt(entries)
    assert "1" in srt
    assert "00:00:00,000 --> 00:00:01,500" in srt
    assert "你好世界" in srt

def test_build_srt_multiple_entries():
    pipe = AudioPipeline(silence_ms=200, lufs=-16)
    entries = [
        {"storyboard_id": "sb_001", "text": "第一句", "start_time": 0.0, "end_time": 1.0},
        {"storyboard_id": "sb_002", "text": "第二句", "start_time": 1.2, "end_time": 2.5},
    ]
    srt = pipe.build_srt(entries)
    assert "第一句" in srt
    assert "第二句" in srt
    assert "00:00:01,200 --> 00:00:02,500" in srt

def test_accumulate_timestamps_no_silence():
    pipe = AudioPipeline(silence_ms=0, lufs=-16)
    segments = [
        Seg(storyboard_id="sb_001", speaker="narrator",
            duration=2.0, raw_timestamps=[
                {"text": "你好", "start_time": 0.0, "end_time": 2.0}
            ]),
        Seg(storyboard_id="sb_002", speaker="narrator",
            duration=1.5, raw_timestamps=[
                {"text": "世界", "start_time": 0.0, "end_time": 1.5}
            ]),
    ]
    result = pipe.accumulate_timestamps(segments)
    assert result[0]["start_time"] == pytest.approx(0.0)
    assert result[0]["end_time"] == pytest.approx(2.0)
    assert result[1]["start_time"] == pytest.approx(2.0)
    assert result[1]["end_time"] == pytest.approx(3.5)

def test_accumulate_timestamps_with_silence_on_speaker_change():
    pipe = AudioPipeline(silence_ms=200, lufs=-16)
    segments = [
        Seg(storyboard_id="sb_001", speaker="narrator",
            duration=2.0, raw_timestamps=[{"text": "a", "start_time": 0.0, "end_time": 2.0}]),
        Seg(storyboard_id="sb_002", speaker="char_001",
            duration=1.0, raw_timestamps=[{"text": "b", "start_time": 0.0, "end_time": 1.0}]),
    ]
    result = pipe.accumulate_timestamps(segments)
    # 切换 speaker → 插入 200ms 静音
    assert result[1]["start_time"] == pytest.approx(2.2)
    assert result[1]["end_time"] == pytest.approx(3.2)
```

- [ ] **Step 2: 运行确认失败**

```bash
pytest tests/audio/test_pipeline.py -v
```

Expected: FAIL

- [ ] **Step 3: 创建 `tests/audio/__init__.py` 和 `src/novel2media/audio/__init__.py`（空文件）**

- [ ] **Step 4: 实现 pipeline.py**

```python
# src/novel2media/audio/pipeline.py
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class AudioSegment:
    storyboard_id: str
    speaker: str
    duration: float                     # 秒
    raw_timestamps: list[dict[str, Any]] # TTS 返回的原始时间戳（从 0 开始）


class AudioPipeline:
    def __init__(self, silence_ms: int, lufs: int) -> None:
        self._silence_ms = silence_ms
        self._lufs = lufs

    def accumulate_timestamps(self, segments: list[AudioSegment]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        offset = 0.0
        prev_speaker: str | None = None

        for seg in segments:
            if prev_speaker is not None and seg.speaker != prev_speaker:
                offset += self._silence_ms / 1000.0
            for ts in seg.raw_timestamps:
                result.append({
                    "storyboard_id": seg.storyboard_id,
                    "text": ts["text"],
                    "speaker": seg.speaker,
                    "start_time": round(ts["start_time"] + offset, 3),
                    "end_time": round(ts["end_time"] + offset, 3),
                })
            offset += seg.duration
            prev_speaker = seg.speaker

        return result

    def build_srt(self, entries: list[dict[str, Any]]) -> str:
        lines: list[str] = []
        for i, entry in enumerate(entries, start=1):
            start = self._fmt_srt_time(entry["start_time"])
            end = self._fmt_srt_time(entry["end_time"])
            lines.append(f"{i}\n{start} --> {end}\n{entry['text']}\n")
        return "\n".join(lines)

    @staticmethod
    def _fmt_srt_time(seconds: float) -> str:
        ms = int(round(seconds * 1000))
        h = ms // 3_600_000
        ms %= 3_600_000
        m = ms // 60_000
        ms %= 60_000
        s = ms // 1000
        ms %= 1000
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    def concat_and_normalize(
        self,
        audio_b64_list: list[str],
        speakers: list[str],
        output_path: Path,
    ) -> None:
        """拼接多段 base64 WAV 并归一化到 self._lufs LUFS，写出到 output_path。
        依赖 pydub + ffmpeg，仅在集成场景使用。"""
        import base64
        import io
        from pydub import AudioSegment as PydubSeg
        from pydub import effects

        combined: PydubSeg | None = None
        prev_speaker: str | None = None

        for b64, speaker in zip(audio_b64_list, speakers):
            wav_bytes = base64.b64decode(b64)
            seg = PydubSeg.from_wav(io.BytesIO(wav_bytes))
            if combined is None:
                combined = seg
            else:
                if speaker != prev_speaker:
                    silence = PydubSeg.silent(duration=self._silence_ms)
                    combined = combined + silence + seg
                else:
                    combined = combined + seg
            prev_speaker = speaker

        if combined is None:
            return

        normalized = effects.normalize(combined, headroom=(-self._lufs - 3))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        normalized.export(str(output_path), format="wav")
```

- [ ] **Step 5: 运行确认通过**

```bash
pytest tests/audio/test_pipeline.py -v
```

Expected: 4 passed

- [ ] **Step 6: Commit**

```bash
git add src/novel2media/audio/ tests/audio/
git commit -m "feat: 音频时间戳累加、SRT 生成、拼接归一化工具"
```

---

## Task 8：全量测试验证

- [ ] **Step 1: 运行全部测试**

```bash
pytest tests/ -v
```

Expected: 全部通过（约 14 个测试），0 failures

- [ ] **Step 2: 验证包可导入**

```bash
python -c "from novel2media.state import GraphState, ChapterStatus; from novel2media.config import ServicesConfig; from novel2media.clients.tts import TTSClient; from novel2media.clients.comfyui import ComfyUIClient; from novel2media.audio.pipeline import AudioPipeline; print('OK')"
```

Expected: 输出 `OK`

- [ ] **Step 3: 最终 Commit**

```bash
git add .
git commit -m "chore: Plan A 基础层实现完成，全量测试通过"
```

---

## 自检

| Spec 要求 | 任务覆盖 |
|-----------|---------|
| GraphState 所有字段 | Task 4 ✅ |
| ServicesConfig 从 services.json 加载 | Task 2 ✅ |
| structlog 日志 | Task 3 ✅ |
| ChatTTS 客户端（重试） | Task 5 ✅ |
| ComfyUI 客户端（轮询/下载） | Task 6 ✅ |
| 音频时间戳累加（全局偏移） | Task 7 ✅ |
| SRT 生成（每 storyboard 一条） | Task 7 ✅ |
| speaker 切换插入静音 | Task 7 ✅ |
| ffmpeg 拼接归一化（-16 LUFS） | Task 7 ✅（集成路径）|
| langgraph.json 配置 | Task 1 ✅ |

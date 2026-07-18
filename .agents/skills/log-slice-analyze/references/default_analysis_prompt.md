# 默认日志排查提示词

当用户没有提出具体问题时，依据这套提示词对截取出的日志切片进行排查。
目标是：从一堆日志行里，快速定位"哪里出了问题 / 哪里卡住 / 哪里有异常"，给出可执行的判断。

## 排查思路（按这个顺序看）

### 1. 先看 error / warning 级别行
日志行里有 `[error]`、`[warning]` 标记。error 是实锤故障，warning 是可疑信号。
重点关注：
- `Run ... failed: <异常类>` —— 图执行抛错，异常类型（KeyError/TypeError/FileNotFoundError/ValueError）直接指向根因方向。
- 紧跟的 `Traceback` —— 看 `File "...", line N, in <函数>` 栈顶，那是真正抛错的位置。
- `interrupt 解析到 unknown 叶子节点` —— LangGraph interrupt 没解析到叶子节点名，前端交互面板会不弹窗。

### 2. 看 HTTP 请求的状态码
日志里大量 `HTTP Request: POST <url> "HTTP/1.1 <状态码>"`。
- `5xx`（500/502/503）—— 后端服务（ComfyUI/TTS/ARK）内部错误，对照 URL 看是哪个服务挂了。
- `4xx`（400/404）—— 请求参数/路径不对，通常是调用方传错。
- `200` —— 正常。
对照 URL 前缀判断服务：
- `comfy.local:8188` → ComfyUI（图片生成）
- `tts.local:9000` → TTS（语音）
- `ark.cn-beijing.volces.com` → 火山引擎 ARK（LLM/Doubao）

### 3. 看业务节点流程是否完整
structlog 日志带 `node=<节点名>` 字段，能看出图走到哪：
- `load_config 完成` → `parse_characters_llm: 完成` → `review_initial_characters` → `setup_dispatcher` → `upload_tri_view` → ...
- 如果流程在某节点后戛然而止（没有后续节点日志），大概率是卡在那里：要么 interrupt 等人（看有没有 waiting_human），要么抛错（看 error）。
- 节点名对照（novel2media 项目）：init_nodes / setup_nodes / chapter_nodes / image_nodes / audio_nodes。

### 4. 看时间间隔
同一 run 内相邻日志的时间戳间隔。正常应秒级。如果某两行间隔几分钟以上，可能是：
- LLM/ComfyUI/TTS 调用慢（外部服务卡）。
- interrupt 在等人（用户没点 pass/revise）—— 这种不是 bug，是等输入。

## 输出格式

排查完给用户这样的结构化结论：

```
## 排查结论

### 发现的问题
（按严重度排序，每条：现象 → 根因 → 证据日志行号/内容 → 建议处理）

### 可疑但未确认的点
（不能完全定性的，列出来让用户判断）

### 流程完整性
（图走到了哪个节点、是否完整、有没有中断）
```

注意：
- 引用证据时带上日志切片里的行号或时间戳，让用户能回去核对。
- 区分"真 bug"和"等用户输入"（interrupt 暂停不是 bug）。
- 如果切片里一切正常（全是 200、流程完整、无 error），直接说"切片内未见异常"，不要硬编问题。

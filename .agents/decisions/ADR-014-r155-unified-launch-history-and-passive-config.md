# ADR-014：R15.5 统一启动、被动配置同步与历史回看边界

- **状态**：Accepted；用户已于 2026-11-23 人工验收
- **日期**：2026-11-23
- **决策人**：用户授权执行 R15.5
- **前置基线**：`v2.0-p5-stable` / `b1215d9`
- **L1 安全快照**：`pre-fix-r155-20261123` / `23c744e`
- **对应执行书**：`体感bug修复文档.md`
- **验收结论**：统一启动、历史回看、被动配置同步和单主字幕连接行为已通过
  用户验收；最终阶段收口另见 R15.5S 标签。

## 背景

Phase 5 后连续使用暴露出五类跨层问题：控制台缺少原生悬浮窗主入口；空态占位、
历史截断和滚动条误触破坏回看；开发态重复同步/编译和双后端导致启动慢；
设置控件不即时同步且 Rust/Python 可能双写配置；重复启动与残留进程会回退旧页面。

R15.5 必须同时覆盖 Shell、Python FastAPI、Rust Tauri 和 React 前端，但不能改变
Phase 5 的单窗单流、零计量、零标红、零音效和禁止 `useSubtitleTail` 边界。

## 决策

### 1. 一个后端，两个兼容入口

1. 普通入口默认打开 `/settings`，控制台顶部「启动实时翻译」调用
   `POST /api/overlay/launch`。
2. `启动同传系统.command` 与 `启动桌面悬浮窗.command` 保持兼容；桌面入口通过
   `HARRY_ATTACH_BACKEND_URL=http://127.0.0.1:<port>` 附着现有 V2 后端。
3. Rust Sidecar 管理器用 `owns_child` 区分自有后端与附着后端。附着实例退出时
   只能关闭自身 Tauri 窗口，不能终止共享 Python 后端。
4. 启动器使用 `.agents/launch.lock`、`.venv-v2.lockhash` 和
   `cargo-build.fingerprint` 串行化快速双击，并在输入未变化时跳过 `uv sync`
   与 `cargo build`。
5. 运行实例身份由 `service`、`project_root`、`pid`、`port`、
   `frontend_fingerprint` 和 `instance_token` 共同校验。只允许清理经项目根目录、
   进程命令行和实例身份确认的旧版或半死进程；禁止盲删共享 `runtime_port`。

### 2. 健康、运行态与停机

1. 启动探针固定使用 `/api/health?fast=1`，快速路径不得枚举 CoreAudio；
   完整 `/api/health` 继续保留音频设备诊断兼容性。
2. `.agents/runtime_state.json` 原子写入，并通过 `fcntl.flock`、PID、端口和
   `instance_token` 三元校验保护跨进程替换与删除。
3. 活跃 WebSocket 统一登记；停机先关闭活跃连接，再设置 Uvicorn 退出。
   `timeout_graceful_shutdown=1.5`，验收要求活跃连接下 2 秒内退出。
4. HTML 页面响应固定 `Cache-Control: no-store, max-age=0` 与
   `Pragma: no-cache`，避免浏览器复用旧版页面。

### 3. 被动配置通道

1. `/ws/config` 是只读配置订阅通道，拥有独立 socket 集合；绝不调用
   `SessionManager.connect_client()`，绝不启动音频、主字幕会话或上游 Qwen。
2. 配置写入先广播 `config_updated` 与最新 `needs_setup`，再执行必要的会话
   reload，避免弹窗和控制台被主链重连阻塞。
3. 音频输入接口在成功切换和 `loopback_missing` 安全回退后都广播配置；
   仍不得因配置页面订阅而建立字幕客户端。
4. 设置保存采用“累积 patch + 分档防抖 + 单一串行队列”；即时控件 `0ms`，
   滑块 `60ms`，文本 `300ms`。旧响应不得覆盖更新后的草稿或造成滑块回弹。
5. 保存失败保留草稿并提供显式“重试同步”；正常状态不恢复手动保存按钮。

### 4. 唯一主字幕连接与显隐

1. 原生窗口显隐以 Rust `overlay_get_visibility` 和
   `overlay-visibility-changed` 为准，前端初始可见状态必须是未知而非乐观 `true`。
2. 只有 `needs_setup=false` 且窗口确认可见时才连接唯一 `/ws/main`；查询失败
   保守按隐藏处理，禁止后台误采音或误推流。
3. 隐藏时关闭主字幕 socket，但保留 `SubtitlePipelineState` 历史；再次显示时
   只重连同一 `main` 通道。
4. 原生控制服务只监听 `127.0.0.1`，提供 `/health`、`/show`、`/hide`；
   窗口动作投递到 Tauri 主线程。Python 只接受精确的
   `http://127.0.0.1:<port>` 控制地址。
5. 配置广播调用 `overlay_sync_presentation(..., persist=false)`，禁止 Rust
   因任意 `config_updated` 反向覆写 `user_settings.json`；用户主动几何操作仍
   由正式几何路径持久化。

### 5. 历史、滚动与视觉纯净

1. Timeline 保留最多 `MAX_TIMELINE_ENTRIES=1000` 条历史，不再按
   `visible_subtitle_count` 硬截断 DOM。配置字段暂时保留用于兼容旧文件，
   但控制台删除无效的“显示条数”控件。
2. 非实时跟随状态采用底部追加锚点，新增字幕后保持原 `scrollTop`；
   不把用户正在回看的行向下推走。
3. 只有回到实时底部边缘或显式“回到实时”才恢复跟随；普通向下滚轮不得强拉底。
4. 空态和未译侧干净留白，不渲染“原文将在这里实时出现”等占位。
5. 分句线仅在 `show_subtitle_dividers=true`、非沉浸且非最后一条时显示；
   主次字重仍完全由 `subtitle_emphasis` 控制。
6. 历史卡片使用 memo 和必要依赖测量，避免字幕 delta 时对 1000 张卡片全量调用
   `getBoundingClientRect()`。

## 后果

- 普通控制台、桌面入口和托盘都围绕同一个 V2 Python 后端，不得再形成双后端。
- 配置页面可以实时随动，但配置 WebSocket 永远不是字幕客户端，不能触发计费。
- 原生字幕浮窗隐藏后保留历史但停止主链；再次显示必须复用单 `main` 会话。
- 旧 `visible_subtitle_count` 配置键保留兼容，但不得重新解释为历史截断策略。
- 因本轮未获 R16 授权，未新增 AGC/VAD、导出、打包或 Phase 6 能力。

## 必须保持不变

- `MAX_OVERLAY_WINDOWS=1`。
- 单一主字幕会话与单一活跃上游 Qwen WebSocket。
- 原生主字幕窗零按钮、零链接。
- 零 Token、`usage`、费用字段；零字幕标红/异常徽章；零音效与
  `AudioContext`/`<audio>`。
- 禁止创建或引用 `useSubtitleTail`。
- `app/audio/**`、`app/engines/**`、`app/glossary/**`、`app/bypass/**`、
  `app/transcript_cleaner.py`、`app/session_manager.py` 本轮零修改。

## 验证

- `tests/unit/test_repro_r155_ux_and_launch.py`
- `tests/unit/test_r155_bootstrap_runtime.py`
- `frontend/src/store/settingsStore.test.ts`
- `frontend/src/screens/SettingsView.test.tsx`
- `frontend/src/components/settings/SubtitlePanel.test.tsx`
- `frontend/src/windows/overlay/OverlayWindow.test.tsx`
- `frontend/src/windows/overlay/timeline/Timeline.test.tsx`
- `frontend/src/windows/overlay/timeline/overlayModel.test.ts`
- `frontend/src/windows/overlay/interaction/overlayContentDrag.test.ts`
- `cargo test --offline --manifest-path src-tauri/Cargo.toml`
- `cargo build --offline --features custom-protocol --manifest-path src-tauri/Cargo.toml`
- `npm --prefix frontend run typecheck && npm --prefix frontend test && npm --prefix frontend run build`
- `.venv-v2/bin/python -m pytest -q tests/`
- `.venv-v2/bin/python -m pytest -q tests/core_regression/`
- `.venv-v2/bin/python scripts/smoke_e2e.py --mock`
- `./scripts/watchdog_gate.sh tests/unit/test_repro_r155_ux_and_launch.py pre-fix-r155-20261123`

R15.5 自动门禁与用户真机验收均已完成。R15.5S 发现的原生全屏、总结图标
和退出生命周期问题使用独立决策 `ADR-015` 封存，不回写本 ADR 已验收的
启动与配置边界。

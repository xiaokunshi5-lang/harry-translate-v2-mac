---
schema_version: 1
contract_version: 0.5.3
contract_status: CONFIRMED
project_name: "HARRY Translate V2 Mac"
initialized_at: "2026-11-22"
confirmed_at: "2026-11-22"
confirmed_by: "user"
last_amended_at: "2026-11-24"
project_first_principle: "在最初版的同传目录内交付稳定、可回滚、可逐轮实测的 Apple Silicon Mac 实时同传系统"
quality_priority_1: "跨机可复现、开箱即用与稳定性"
quality_priority_2: "极速低延迟出字"
quality_priority_3: "Mac 单窗双语显示层级、沉浸体验与语义精修"
perf_critical: true
audio_sensitive: true
ui_contract_enabled: true
---

# 项目专属契约 (PROJECT_CONTRACT)

> 本契约是 CANONICAL SPEC v2.1 在当前项目仓库根目录内的 Mac-only 覆盖层。用户本次明确要求优先于原始计划中“独立新仓、双平台对等、Windows 接力”的旧条款。

## 0. 事实、推断与确认

| 编号 | 内容 | 来源 | 状态 | 确认人 / 日期 |
| :--- | :--- | :--- | :--- | :--- |
| F-01 | 质量排序严格为 `P1 稳定性与可复现 > P2 极速低延迟 > P3 Mac 悬浮体验与语义精修` | 用户批准计划、`ADR-006` | `[已确认]` | 用户 / 2026-11-22 |
| F-02 | 唯一实现目录是本项目仓库根目录；外部计划目录只读，不新建桌面 V2 仓库 | 用户本次指令 | `[已确认]` | 用户 / 2026-11-22 |
| F-03 | 仅交付 Apple Silicon Mac 版，最低 macOS 13；不实现 Windows 启动器、音频后端、打包、测试或对齐 | 用户本次指令 | `[已确认]` | 用户 / 2026-11-22 |
| F-04 | 保留 7 Phase、17 轮结构；每轮自动验证后暂停，用户明确通过后才进入下一轮 | 用户本次指令 | `[已确认]` | 用户 / 2026-11-22 |
| F-05 | V1 当前使用 `CHUNK_SIZE=3200`、PyAudio、串行广播、Token/`usage` 统计和 `fetchStats()` 轮询 | `server.py`、`index.html` 静态审计 | `[已验证]` | — |
| F-06 | 常驻词库收敛为 `00_base_emergency` 与 `01_curated_everyday` 两包共 250 词；`glossary.json` 只保留用户自定义词 | 用户批准计划、`ADR-008` | `[已确认]` | 用户 / 2026-11-23 |
| F-07 | 字幕画面禁止任何自动标红、下划线或异常徽章；旁路未译词只可触发后台静默加急 | 用户批准计划、`ADR-003` | `[已确认]` | 用户 / 2026-11-22 |
| F-08 | `hayamimi` 许可元数据不明确，不复制其源码 | 用户批准计划、许可审计 | `[已确认]` | 用户 / 2026-11-22 |
| F-09 | `REV-01` 至 `REV-06` 在对应轮次开工前按门禁核验并写入 `.agents/decisions/` | 用户批准计划、`D6` | `[已确认]` | 用户 / 2026-11-22 |
| F-10 | 任意时刻只允许一个主翻译窗与一条活跃上游 Qwen WebSocket；旧 `/en`、`/zh` 仅单路兼容 | 用户批准 R09.5、`ADR-008` | `[已确认]` | 用户 / 2026-11-23 |
| F-11 | 上游关闭前发送 `session.finish` 并最多排空 `250ms`；单窗消费 `source_font_size`、`target_font_size`、`subtitle_emphasis`、`bilingual_layout_order` | 用户批准 R09.5、`ADR-008` | `[已确认]` | 用户 / 2026-11-23 |
| F-12 | Phase 4（R10-R12）已完成；R12 由用户确认验收成功，阶段基线为 `a8f514f` 与 `v2.0-p4-stable` | 用户确认、`ADR-009` | `[已确认]` | 用户 / 2026-11-23 |
| F-13 | 原生浮窗零按钮/零链接；沉浸与锁定穿透只通过 `harry-tray` 或独立浏览器配置页恢复 | 用户批准 R10.5、`ADR-009` | `[已确认]` | 用户 / 2026-11-23 |
| F-14 | 系统音源先探测回环设备；缺失时返回 `loopback_missing` 并回退麦克风，不宣称已完成原生 Process Tap | 用户批准 R11、`ADR-006`、`ADR-009` | `[已确认]` | 用户 / 2026-11-23 |
| F-15 | Phase 5（R13-R15）已完成并验收；R15 自动门禁全绿后进入阶段收官，阶段代码基线为 `b1215d9` | 用户验收与收官指令、`ADR-010`、`ADR-011`、`ADR-013` | `[已确认]` | 用户 / 2026-11-23 |
| F-16 | Phase 5 保留唯一主字幕会话；静默纠错与默认关闭总结只能走有界异步旁路，禁止新增第二字幕/上游 WebSocket | 用户批准 Phase 5、`ADR-011`、`ADR-013` | `[已确认]` | 用户 / 2026-11-23 |
| F-17 | 阶段验收后的 R15.5/R15.5S 已由用户验收；`fix-attempt/R155S/2`（`a77c38e`）为最终候选，收口标签为 `checkpoint-r155s-ok` 与 `post-fix-R155S`，不移动 `v2.0-p5-stable` | 用户最终验收、`ADR-014`、`ADR-015` | `[已确认]` | 用户 / 2026-11-23 |
| F-18 | R16 已完成自动与人工收口，标签为 `checkpoint-r16-ok` / `7583c4d`。MD/SRT/JSON 用户侧下载/打开尚未开放，用户决定暂不修复并作为已知待办保留 | 用户验收反馈、`ADR-017`、阶段 6 handoff | `[已确认]` | 用户 / 2026-11-24 |
| F-19 | R17 已由用户授权并完成 AC-19/AC-20 终审压测、合规审计、全量回归和本地出厂构建。长会话压测发现并修复清洗器无界段落状态；`ADR-018` 记录语义差分与有界状态决策 | 用户执行授权、`ADR-018`、`docs/RELEASE_REPORT_v2.0.md` | `[已确认]` | 用户 / 2026-11-24 |
| F-20 | R18-01 至 R18-04 已全部由用户人工验收通过。收口标签为 `checkpoint-r18-01-ok`、`checkpoint-r18-02-ok`、`checkpoint-r18-03-ok`、`checkpoint-r18-04-ok` 和 `post-fix-R18`；候选提交为 `47c3480`。公开发布使用新标签 `v2.0-r18-ready`，不移动 `v2.0-mac-ready` | 用户全项验收、`ADR-019` 至 `ADR-022`、`docs/RELEASE_REPORT_R18.md` | `[已确认]` | 用户 / 2026-11-24 |

## 1. 第一要义、成功与失败

- **第一要义**：在同一个 Mac 工作目录内，把现有 V1 逐轮升级为稳定、可复现、可回滚的 Apple Silicon 实时同传系统；任何速度或视觉收益都不得破坏稳定性与数据安全。
- **核心用户 / 场景**：在会议现场使用 Apple Silicon Mac 的演讲者或控台人员，需要真实麦克风输入、低延迟双语字幕、可恢复的本地配置和明确的故障边界。
- **成功标准**：每一轮有可执行证据、可回滚 checkpoint 和人工复测步骤；无连接时零云端推流；配置与热词原子持久化；250 条精选词冷启动 `< 5ms` 且线载荷 `<= 6KB`；单窗双语同屏可独立调节原文/译文字号与主次；最终 arm64 `.dmg` 可在干净 Mac 冷启动。
- **非目标**：不处理 Windows 代码、构建、对齐或交接；不创建 `v2.0-stable`；不实现 Token、`usage`、费用统计业务事件；不在字幕画面做自动标红；不复制 AGPL-3.0 或 `hayamimi` 源码。
- **反目标**：关窗后仍向上游推流；密钥进入 Git、日志或前端存储；字幕标红；测试被跳过或伪造；热词方向反转；配置损坏；用 Mock 结果冒充真实 API 或真机结论。

### CANONICAL SPEC v2.1 核心决议

| 编号 | 决策 | 本项目落地边界 |
| :--- | :--- | :--- |
| `ADR-001` | 原地演进、Apple Silicon only、严格逐轮停测 | 不新建仓库，不改外部计划文档，不处理 Windows，不创建 `v2.0-stable` |
| `ADR-002-v2.2` | 单窗双语同屏与显示主次 | `MAX_OVERLAY_WINDOWS=1`；独立原/译字号、主次权重与上下顺序 |
| `ADR-003` | 快捷临时词与会话注入 | 浮窗和控台可快速注入，字幕画面零标红、零异常徽章 |
| `ADR-004` | Tauri v2 + React/Mimi + Python FastAPI Sidecar | 只构建 arm64 `.dmg`；Mimi 固定 `bce7164`；不复制 `hayamimi` |
| `ADR-005-v2.2` | Mac 单平台优先与 Mimi 沉浸/穿透 | R10-R12 原生单窗、跨 Space 与可恢复点击穿透已落地并通过 Phase 4 验收 |
| `ADR-006-v2.2` | 精选热词与零 Token 计量 | 250 词、4KB 默认线预算、零 `<audio>`、零字幕标红 |
| `ADR-012` | Qwen 3.8 抗断连与线预算治理 | 禁发 `qwen3.8` 顶层 `turn_detection`；`<400>` 降级；`1007/429` 退避；10s 健康复位 |

## 2. 有顺序的质量优先级

1. **P1 稳定性、可复现与数据安全**：任何轮次和阶段都必须保留 V1 可回退路径；无连接零推流；TLS 校验完整；密钥仅在本机 `.env` 且权限 `0600`；配置、热词、导出原子写盘。验证命令为全量测试、可靠基线和对应真机步骤，P1 失败即阻断。
2. **P2 极速低延迟**：目标音频切片 50ms，单一主会话常态端到端低于 380ms；`qwen3.8*` 不发送 240ms 顶层 `turn_detection`。`bench_latency.py` 报告 P99，相对基线劣化超过 3% 即阻断。
3. **P3 Mac 悬浮体验与语义精修**：Mimi 14 项、系统声音、Echo 滚动与清洗、静默纠错、默认关闭总结。P3 不得为视觉或语义效果阻塞主链、产生标红或增加空转请求。

**冲突裁决铁律**：严格执行 `P1 > P2 > P3`。`audio_sensitive=true` 是阻断级约束；所有 UI 禁止按键音、提示音和 `AudioContext` 发声节点。

## 3. 核心链路与必须保持的行为

| 编号 | 核心链路 / 场景 | 入口与关键文件 | 必须保持的行为 | 允许的退化 | 验证命令或测试 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| C-01 | V1 基线启动、配置与回滚 | `启动同传系统.command`, `start.sh`, `server.py`, `.env`, `scripts/rollback.sh` | `.env` 不展示、不提交；旧入口在 R07 前可运行；回滚先创建 rescue 分支并保护本地资产 | 缺有效 Key 时停止云端连接并提示配置 | `tests/unit/test_smoke_bootstrap.py`, `tests/unit/test_rollback_scripts.py` |
| C-02 | 零编译音频、零连接停推与并发广播 | `app/audio/pipeline.py`, `app/session_manager.py`, `app/engines/qwen_realtime.py` | 800 样本/50ms 切片；有界队列；零活跃窗口不推流；慢端不阻塞正常端 | 音频设备缺失时返回结构化错误并保持服务可启动 | `tests/core_regression/test_main_fastpath.py` |
| C-03 | 250 条精选词、线预算与 QuickTermBar | `app/glossary/store.py`, `app/glossary/engine.py`, `frontend/src/components/glossary/QuickTermBar.tsx` | 同步装载；P0/P1/P2 排序；UTF-8 默认 4KB、硬上限 6KB；快捷会话词不标红 | 热词关闭或 `<400>` 时退化为无热词极简握手 | `tests/core_regression/test_glossary_priority.py`, `tests/core_regression/test_qwen_adapter.py` |
| C-04 | 零侵入旁路与默认关总结 | `app/bypass/patch_worker.py`, `app/bypass/summary_worker.py` | 主链只同步入有界队列；纠错使用 `source_hash` 乐观锁；`summary_enabled=false` 时 0 请求、0 线程、0 DOM、0 总结窗口 | 纠错失败保留原文；总结失败降级 `rules_fallback` | `tests/core_regression/test_bypass_isolation.py`, `tests/core_regression/test_summary_isolation.py` |
| C-05 | Web 单窗双语与显示层级 | `frontend/src/screens/MonitorScreens.tsx`, `SubtitlePanel.tsx`, `PreviewStage.tsx` | 仅一个 `ChannelSocket`；原/译独立字号；主次仅字重/透明度；零标红 | 原生壳异常时回退 `/hud?fallback=browser`，但不得建立第二上游 | `tests/unit/test_frontend_structure.py`, `tests/smoke/settings_ui_gate.py` |
| C-06 | Mac 原生单悬浮窗、托盘逃生与 Sidecar 生命周期 | `src-tauri/src/windows/overlay.rs`, `tray.rs`, `src-tauri/src/sidecar.rs`, `frontend/src/windows/overlay/OverlayWindow.tsx` | `MAX_OVERLAY_WINDOWS=1`；原生零按钮/零链接；穿透公式 `immersive || (locked && click_through)`；退出回收 Sidecar | 无回环设备时系统音源回退麦克风；无 Tauri 时回退浏览器 HUD | `tests/unit/test_desktop_panel.py`, `tests/unit/test_repro_r105_pure_overlay.py`, `cargo test --offline --manifest-path src-tauri/Cargo.toml` |
| C-07 | 快速切语、健康探测与防串句 | `app/engines/qwen_realtime.py`, `app/session_manager.py`, `frontend/src/lib/subtitles/languagePurity.ts`, `LanguageStatusCapsule.tsx` | PING/PONG 10s/4s；READY 连续健康 10s 才复位退避；`session.finish <=250ms`；`session_reset` 清残句；Web 显示真实健康态 | `<400>` 时无热词降级；系统音源缺失只停 system 通道 | `tests/core_regression/test_single_window_stability.py`, `frontend/src/lib/subtitles/languagePurity.test.ts` |
| C-08 | R16 物理麦克风 AGC、Qwen 3.8 安全 commit 与单会话导出基础 | `app/audio/pipeline.py`, `app/engines/qwen_realtime.py`, `app/transcript_exporter.py`, `app/main.py` | AGC 只处理物理麦克风；系统流直通；commit 与 append 共用有界队列；`qwen3.8*` 零顶层 `turn_detection`；内部原子保存/格式化保持零 Token/usage/费用 | 缺回环设备仍回退麦克风；MD/SRT/JSON 用户侧下载/打开暂不开放，不得宣称为已完成用户能力 | `tests/core_regression/test_dual_audio_dsp.py`, `tests/unit/test_exporter.py`, `tests/unit/test_package_smoke.py` |
| C-09 | R17 长会话清洗、1000 卡片、WS 与出厂门禁 | `app/transcript_cleaner.py`, `tests/bench/test_soak_benchmark.py`, `frontend/src/windows/overlay/timeline/TimelineSoak.test.tsx` | 只保留当前段落有界窗口和单调段落号；卡片硬上限 1000；后/前 100 句耗时比 `<=1.2`；250 词冷启动 `<5ms`；单窗与 250ms 收尾不退化 | 不缩小句子数、不放宽 1.2x/5ms/250ms/3% 阈值；真实设备与联网边界仍单列 | `tests/bench/test_soak_benchmark.py`, `frontend/src/windows/overlay/timeline/TimelineSoak.test.tsx` |
| C-10 | R18 安静启动、权威实时翻译滑钮与原生窗口生命周期 | `src-tauri/src/windows/settings.rs`, `src-tauri/src/windows/tray.rs`, `app/main.py`, `frontend/src/screens/SettingsView.tsx` | 冷启动只显示原生 `settings` 窗口且 `overlay` 隐藏；顶栏只有一个由后端真实状态驱动的实时翻译滑钮；Dock、Finder、菜单和 `Cmd+,` 统一恢复设置窗；显式退出回收 Sidecar 与音频；`/ws/config` 仍为被动通道 | 无 API Key 时滑钮禁用并说明原因；关闭设置窗只隐藏，不停止当前翻译；辅助大屏只打开外部浏览器 | `tests/unit/test_repro_r18_01_quiet_launch_and_stop.py`, `tests/unit/test_repro_r18_02_realtime_switch.py`, `tests/unit/test_repro_r18_03_native_settings_lifecycle.py` |

## 4. 设计意图账本

| 编号 | 文件 / 符号 | 看起来不完美之处 | 服务的第一要义 | 依赖的上下文与上下游 | 禁止的简化或重构 | 证据 / ADR |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| D-01 | `app/audio/pipeline.py` TX 队列 | 队列满时丢弃最旧帧 | 宁可丢旧帧也不让采集线程被网络背压阻塞 | 麦克风回调与网络发送速率不一致 | 不得改成无界队列或阻塞写入 | `ADR-006` |
| D-02 | `app/audio/pipeline.py` RMS 降级 | 同时保留 `audioop`、`audioop_lts` 与 `array('h')` | Python 版本变化时仍可启动 | Python 3.12 与后续运行时兼容 | 不得顶层硬依赖已移除模块 | `ADR-006` |
| D-03 | `app/glossary/store.py` 精选同步装载 | 常驻词库只有 250 条，看起来容量很小 | 先稳定出字，再按预算精修 | 本地 Trie、线预算、P0/P1/P2 排序 | 不得恢复 18k 合成词包或后台扩展包线程 | `ADR-008` |
| D-04 | `app/bypass/` 有界异步队列 | 队列满时回收最旧任务，失败时保留可见原文 | 纠错和总结绝不能阻塞或拖慢字幕主链 | Qwen 主链、后台 worker、前端乐观锁、默认关总结 | 不得在主协程 `await` LLM 旁路；不得新增第二字幕流 | `ADR-003`, `ADR-006`, `ADR-011`, `ADR-013` |
| D-05 | Git checkpoint 与 rescue 分支 | 每轮必须停测、每次回滚先保存现场 | 让用户逐轮实测且永不丢失本地证据 | 回合执行、用户确认、`rollback.sh` | 不得自动跨轮、不得无参回滚或删除受保护资产 | `ADR-001` |
| D-06 | `src-tauri/src/sidecar.rs` stdout/health 握手 | 比直接启动 Python 多一层进程状态 | 防止孤儿进程、端口冲突和假就绪 | Tauri 宿主、开发态 `.venv-v2`、发布态 externalBin | 不得用固定端口或忽略子进程回收 | `ADR-004`, `ADR-009` |
| D-07 | `app/audio/device_probe.py` 只探测不打开流 | 看起来像“没有真正采集系统声音” | 先让单麦可用并提供明确故障边界 | 音频后端、设置页、BlackHole/MIDI 手工配置 | 不得在无回环设备时伪装双音源可用 | `ADR-006`, `ADR-009` |
| D-08 | `/ws/main` 与 `/ws/subtitles` 双别名 | 路由看起来重复 | 兼容旧页面且保持唯一上游事实来源 | 前端 `ChannelSocket`、R12 执行书、旧兼容路由 | 不得让两个别名同时建立两路上游会话 | `ADR-008`, `ADR-009` |
| D-09 | `app/transcript_exporter.py` 的 `clientSavedAt` 与原子写 | 多一个版本号、锁和临时文件 | 防止慢请求覆盖新会话，并保证进程退出时记录可恢复 | R16 主链、本地导出、R14 补丁 | 不得改成直接覆盖写或无版本比较 | `ADR-017` |
| D-10 | `app/transcript_cleaner.py` 的当前段落窗口 | 只保留最多 12 句而不是整场句子 | 保持 6/12 行段落语义同时消除长会话 O(N²) 重算 | R13 清洗、SessionManager 定稿、R17 压测 | 不得恢复全量 `_sentences` 历史或每句重算整场 | `ADR-018` |

## 5. 已知良好基线与禁改清单

- **稳定基线**：`v0.1-v1-baseline-stable` 保留为 V1 回退基线；Phase 4 稳定基线为 `v2.0-p4-stable`（`a8f514f`）；Phase 5 实现基线为 `v2.0-p5-stable`（`b1215d9`）；阶段验收修复由 `checkpoint-r155s-ok` 与 `post-fix-R155S` 单独封存；R16 L1 为 `pre-feature-p6-20261123`（`4b60435`），R16 收口为 `checkpoint-r16-ok`（`7583c4d`）；R18 验收候选为 `47c3480`，收口为 `checkpoint-r18-04-ok` 与 `post-fix-R18`。禁止移动旧阶段标签。
- **当前已知失败 / 待开放**：R16 的 MD/SRT/JSON 用户侧下载或打开尚未正常开放；内部原子保存与格式化代码保留，但不得计入已完成的用户能力。用户已决定 R16 暂不修复，后续必须另行授权。R18 发布包仍是 ad-hoc 签名，带 `com.apple.quarantine` 时可能显示“已损坏”；Developer ID 签名、Apple 公证与干净 Mac 零提示启动尚未获得证书环境验证。Phase 4/5 自动化测试仍必须零失败；真实长时间网络闪断、真实 DashScope 旁路质量、原生总结窗口交互与完整原生 Core Audio Process Tap 不得仅凭 Mock 宣称通过。
- **已证伪方案 / 禁止重复尝试**：
  1. `CHUNK_SIZE=3200` 的 200ms 切片不得作为 V2 目标。
  2. `useSubtitleTail` 的 100 至 400ms 憋字不得引入。
  3. 串行三遍 AC 替换不得恢复。
  4. `ssl.CERT_NONE` 或关闭 TLS 校验不得出现。
  5. `nonPersistentDataStore()` 等非持久窗口状态不得使用。
  6. Win11 `wmic`、Windows 启动器和 Windows 对齐不在本项目范围。
  7. 零客户端仍推流或关窗后空转计费不得出现。
  8. 字幕画面自动标红、下划线或异常徽章不得出现。
  9. Token、`usage`、费用统计不得进入业务事件或导出。
  10. AGPL-3.0 和许可不明确的 `hayamimi` 源码不得复制。
  11. `qwen3.8*` 的顶层 `turn_detection` 与超过 `6144 B` 的 corpus 不得恢复。
  12. 首页或后端不得同时建立 `en`/`zh` 两条上游 Qwen 会话。
  13. `/ws/main` 与 `/ws/subtitles` 不得各自建立一条上游会话。
  14. 不得把回环设备探测结果夸大为已实现完整 Core Audio Process Tap 采集。
  15. 不得把可反复关闭的 summary 动态转换为 `NSPanel`，也不得恢复对转换后窗口的 `destroy()` 或 `to_window().close()`；该路径会触发 Tao foreign-exception abort。
- **禁止顺手修改**：`.env`、V1 回退环境 `.venv`、R01 checkpoint、外部计划文档、V1 三个核心文件在 R02 内不得改动。
- **允许变化的边界**：每轮仅修改该轮执行书白名单；所有项目改动必须位于当前项目仓库根目录。

## 6. 证据契约

| 配置字段 | 本项目取值 | 契约要求 |
| :--- | :--- | :--- |
| `CONTRACT_PATH` | `.agents/PROJECT_CONTRACT.md` | 固定，状态必须为 `CONFIRMED` |
| `TEST_CMD` | `.venv-v2/bin/python -m pytest -q tests/` | R02 全量测试真实执行，不得注入 skip |
| `CORE_REGRESSION_CMD` | `.venv-v2/bin/python -m pytest -q tests/core_regression/` | 保护 V1 路由和基线不变量 |
| `SMOKE_CMD` | `.venv-v2/bin/python scripts/smoke_e2e.py --mock` | 验证 JSON/WS 主链帧和无 Token 业务负载 |
| `LINT_CMD` | `.venv-v2/bin/python -m ruff check scripts tests && .venv-v2/bin/python -m compileall -q server.py switch_key.py scripts tests` | 静态检查与语法检查均真实执行 |
| `BENCH_CMD` | `.venv-v2/bin/python scripts/bench_latency.py` | 最后一行只输出合法毫秒数值 |
| `BENCH_THRESHOLD` | `3` | 相对基线劣化上限 3% |
| `BENCH_HIGHER_IS_BETTER` | `0` | 时延越低越好 |
| `PERF_CRITICAL` | `1` | 基准与数值不可豁免 |
| `FAILURE_SIGNATURE_CMD` | 空命令 | 使用工作流脚本的通用稳定失败签名解析 |

基线证据由 `scripts/capture_baseline.sh HEAD` 原子写入 `.agents/baseline/`，该目录本地不入 Git。

## 7. 项目专属工作流路由

| 任务类型 | 唤醒的全局文档 | 本项目额外规则 | 适用 / 裁剪 |
| :--- | :--- | :--- | :--- |
| 新功能 / 重构 | `docs/workflows/计划工作流.md` | 每轮读取总纲、Mac 专项、现行 ADR 和当轮执行书；只改白名单；完成后停测 | 始终 |
| Bug / 性能退步 / 效果纠偏 | `docs/workflows/程序修复工作流.md` | 先复现并建立单根因假设；不得弱化测试；连续两次失败即回退 | 始终 |
| UI / 交互 / 动效 | `docs/workflows/UI审美与开源组件借鉴工作流.md` | `audio_sensitive=true`；字幕零标红；尊重现有 Mac/Mimi 交互 | 进入 UI 轮次时启用 |

## 8. 审查建议裁决规则

| 编号 | 建议 | 裁决 | 代码 / 测试证据 | 对 P1 的影响 | 验证命令 | ADR |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| R-01 | 将 `PERF_CRITICAL` 设为 0 以跳过基准 | 拒绝 | 性能是本项目 P2 硬约束，且用户要求每轮真实验证 | 会允许未度量的性能退化 | `scripts/watchdog_gate.sh tests/unit/test_smoke_bootstrap.py HEAD` | `ADR-006` |
| R-02 | 沿用独立新仓或增加 Windows 对齐轮次 | 拒绝 | 用户明确要求原地实施且仅处理 Mac | 增加迁移与跨平台回归面 | `tests/unit/test_contract_and_config.py` | `ADR-001` |
| R-03 | 将 `hayamimi` 作为依赖或复制源码 | 拒绝 | 许可元数据不明确 | 引入不可审计合规风险 | `git grep -n hayamimi -- ':!*.md'` | `ADR-004` |

裁决硬规则：

1. 与第 3 章必须保持的行为冲突时默认拒绝，除非用户明确修订契约。
2. 改变第 4 章设计意图时，必须读取上下游并做稳定版对照。
3. 一次只验证一个根因假设。
4. 拒绝必须附证据与代价。
5. 交付报告固定分栏：`已修复`、`保持不变`、`尚未验证`、`已拒绝建议及理由`。

## 9. 变更治理

- 契约确认后由用户或明确授权者维护；Agent 可提出修订，不得静默改写第一要义或质量排序。
- 重大架构取舍写入 `.agents/decisions/ADR-xxx.md`；可执行不变量放入 `tests/`。
- 删除或改变不变量提升主版本，新增原则提升次版本，措辞修正提升补丁版本。
- 契约、证据命令、阈值或稳定基线变化后必须重新捕获基线。
- 每轮结束创建 `checkpoint-rXX-ok`；没有用户确认不得开始下一轮。

## 10. 用户确认清单

- [x] 项目第一要义、核心场景、非目标、反目标已由用户确认。
- [x] 质量优先级按 `P1 > P2 > P3` 排序。
- [x] 核心链路、必须保持的行为与允许退化边界可验证。
- [x] 反直觉设计及禁止的简化方式已记录。
- [x] 稳定基线、证据命令、性能阈值和已知失败已填写。
- [x] 所有占位符已删除，契约状态为 `CONFIRMED`。
- [x] 契约、ADR、回归资产将纳入 Git；过程日志和基线保持本地。
- [x] Phase 4（R10-R12）与 Phase 5（R13-R15）已由用户确认验收成功。
- [x] R15.5/R15.5S 原生全屏、总结图标与退出生命周期修复已由用户确认验收成功。
- [x] R16 已收口为 `checkpoint-r16-ok`；MD/SRT/JSON 用户侧导出仍待开放。
- [x] R17 已由用户授权并完成终审压测、清洗器有界状态修复、合规审计、
  全量回归和本地出厂构建。

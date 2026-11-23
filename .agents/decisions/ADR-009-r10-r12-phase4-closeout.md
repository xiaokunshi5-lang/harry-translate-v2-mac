# ADR-009：Phase 4 单窗原生壳收口与稳定性验收

- **状态**：Accepted
- **日期**：2026-11-23
- **决策人**：user
- **确认依据**：用户确认 R12 完成，前四个阶段验收成功
- **代码基线**：`a8f514f4082af2592477fd4b762c44401f93f98f`
- **阶段标签**：`v2.0-p4-stable`

> **后续状态（2026-11-23）**：用户后续已明确授权 Phase 5，R13-R15 已完成
> 并验收。该事实不改变本 ADR 对 Phase 4 的封版范围；当前实现边界另见
> `ADR-010`、`ADR-011`、`ADR-013` 和
> `docs/RELEASE_REPORT_v2.0.md`。阶段 handoff 由用户保存在仓库外的本地
> 过程材料归档中，不属于当前正式版输入。

## 背景

R09.5 已把翻译主链收敛为单窗单流、250 词、6 KB 线预算和 Qwen 3.8 抗断连
基线。Phase 4 需要在此基础上交付 Apple Silicon Mac 原生悬浮窗、Sidecar
生命周期、系统音源降级和长连接稳定性，同时避免重新引入多窗口、多上游、
字幕视觉噪声和关窗后继续推流。

## 决策

1. Phase 4 在 `a8f514f` 收口。`checkpoint-r11-ok` 指向 `c5decd5`；
   `checkpoint-r12-ok` 与 `v2.0-p4-stable` 指向 `a8f514f`。
2. 原生层固定 `MAX_OVERLAY_WINDOWS = 1`，唯一窗口标签为 `overlay`；
   Python `SessionPool.MAX_WINDOWS` 和前端 IPC 指标必须与 Rust 常量一致。
3. 原生浮窗内部严格零按钮、零链接。沉浸或锁定穿透后，只通过
   `harry-tray` 菜单栏或独立浏览器配置页恢复。
4. 有效点击穿透公式固定为 `immersive || (locked && click_through)`。
5. 配置由 Python `config/user_settings.json` 充当单一业务事实来源。
   `config_updated` 负责实时同步，WebSocket 断线时使用 1.5 秒轮询兜底。
6. 系统音源使用前必须探测回环输入设备。缺失时返回 `loopback_missing`，
   回退麦克风并展示 BlackHole 2ch 与多输出设备的配置步骤。
7. 主路由保留 `/ws/main`，并接受 `/ws/subtitles` 作为映射到同一 `main`
   通道的兼容别名；二者不得并行建立两条上游会话。
8. 关闭会话前发送 `session.finish`，最多等待 250ms；未定稿字幕通过
   `session_reset` 代次锁清除，快速切语不得把旧语种残句带入新目标语。
9. Qwen 3.8 的 PING/PONG 健康检查间隔为 10 秒，PONG 等待上限为 4 秒；
   `session.updated` 不清零重连计数，只有连续健康 10 秒后才复位。
10. Phase 5（R13-R15）不得自动开始，必须等待用户明确授权，并先读取本 ADR、
    `ADR-008`、项目契约和阶段 handoff。

## 后果

- 后续所有原生窗口和字幕状态只围绕唯一 `main` 窗口演进。
- 系统声音文档必须区分“回环设备探测/降级”和“完整的原生 Core Audio
  Process Tap 采集”，不得把前者夸大为后者。
- R13-R15 的旁路、滚动和清洗必须复用当前单会话事件边界，不能增加并行
  上游连接。
- 任何恢复多窗、恢复顶层 `turn_detection`、扩大 corpus 或删除托盘逃生
  入口的改动，都需要新的用户决议和 ADR，不能作为普通重构执行。

## 验证

- `tests/unit/test_single_window_config.py`
- `tests/core_regression/test_single_window_stability.py`
- `tests/unit/test_system_audio_probe.py`
- `tests/unit/test_tauri_manifest_contract.py`
- `tests/unit/test_desktop_panel.py`
- `tests/unit/test_repro_r105_pure_overlay.py`
- `cargo test --offline --manifest-path src-tauri/Cargo.toml`
- `cd frontend && npm test && npm run build`
- 历史 Phase 4 验收证据（用户本地过程材料归档）

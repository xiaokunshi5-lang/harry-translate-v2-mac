# ADR-006：Mimi 14 项、Mac 系统声音与零 Token 计量

- **状态**：Accepted
- **日期**：2026-11-22
- **决策人**：user

## 背景

原计划把系统声音放到较晚阶段，且保留 Token、`usage`、费用字段和 `fetchStats()` 轮询。用户要求 Mac 版在 Phase 4 完整对齐 Mimi 视觉能力、前移系统声音、保持字幕纯净，并从所有业务事件中删除用量计量。

## 决策

1. 完整对齐 Mimi 14 项：
   - 实时字幕开关；
   - 沉浸三态：译文、原文、译文加原文；
   - 字幕显示三态；
   - 本机字体枚举；
   - 行距 `1.0x` 至 `2.0x`，默认 `1.32x`，与分句线开关并存；
   - 显示条数 `all` 或 `1..10`，默认 `3`；
   - 系统声音开关、来源选择与独立配色；
   - 麦克风字幕独立配色；
   - 字幕 5 预设色与自定义色；
   - 字号 `14` 至 `48px`；
   - 1% 至 100% 纯背景透明度，文字透明度保持 1；
   - 左、中、右对齐；
   - 锁定位置、真点击穿透与悬停解锁；
   - 原文与译文垂直堆叠和动态高度让渡。
2. macOS 14.2 及以上优先 Core Audio Process Tap；macOS 13 至 14.1 回退 BlackHole 2ch；缺失冲突设备时返回 `loopback_missing`，单麦继续可用。
3. `audio_sensitive=true`：全部 UI 禁止音效和发声节点。
4. 禁止字幕自动标红、下划线和异常徽章。
5. 删除所有 Token、`usage`、`summary_tokens`、`usage_stats`、费用累计和 `/api/stats`。业务事件、WebSocket 帧和导出文件不得含这些字段。
6. 保留并强化 `asyncio.gather` 并发扇出、250ms 慢端隔离，删除 `fetchStats()` HTTP 风暴。
7. 全仓只使用 7 Phase、`R01` 至 `R17` 编号；最终 Mac 标签为 `v2.0-mac-ready`，不创建 Windows 标签或 `v2.0-stable`。

## 后果

- R05 必须证明零连接零推流、并发广播和单次 `session.update`。
- R11 必须证明 14 项、系统声音降级和零音效。
- R13 至 R17 必须持续执行零 Token、零标红、零 AGPL 审计。
- 费用只在文档中以静态倍数说明，不进入运行时计量。

## 验证

- `tests/core_regression/test_main_fastpath.py`
- `tests/smoke/test_hud_contract.py`
- `tests/core_regression/test_bypass_isolation.py`
- R17 全仓 grep、看门狗门禁 0 至 5 和人工 GUI 验收

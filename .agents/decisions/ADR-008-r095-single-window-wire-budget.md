# ADR-008：R09.5 单窗单流、精选词库与 Qwen 3.8 抗断连

- **状态**：Accepted
- **日期**：2026-11-23
- **决策人**：user
- **替代**：`ADR-002` 的全量词库与 `ADR-005` 的多窗口并行方案
- **对应外部决议**：`ADR-002-v2.2`、`ADR-005-v2.2`、`ADR-006-v2.2`、`ADR-012`

## 背景

R09 真机测试已证实：约 109–119 KB 的 `corpus`、`qwen3.8` 不兼容的顶层
`turn_detection`、`session.updated` 立即清零重连退避，以及首页同时建立
`en`/`zh` 两条上游连接，会形成 `<400>` 与 `1007` 叠加的重连风暴。

## 决策

1. `MAX_OVERLAY_WINDOWS = 1`，唯一主窗使用 `/ws/main`；`/ws/en` 与 `/ws/zh`
   仅保留单路兼容入口。任意时刻只允许一个活跃上游 Qwen 会话。
2. `glossary_bank/` 只保留 `00_base_emergency` 锁定包与
   `01_curated_everyday` 精选包，常驻精选词总量 `<= 250`，单条源词
   `<= 48` 字符；装载为同步单步，不创建后台扩展包线程。
3. 云端热词按 `P0 会话词 > P1 用户自定义 > P2 精选包` 排序，UTF-8 JSON
   序列化默认预算 `4096 B`，硬上限 `6144 B`；关闭热词时不发送 `corpus`。
4. `qwen3.8*` 的 `session.update` 禁止顶层 `turn_detection`。无热词时只发送
   `output_modalities=["text"]` 与 `translation.language`。
5. 首次 `<400> InvalidParameter/length` 自动暂停 `corpus`，保留原始错误并
   以极简握手重试；`1007/429` 使用至少 `1s` 起步、上限 `16s` 的指数退避。
6. `session.updated` 不得清零重连计数。只有 READY 连续健康达到 `10s`
   才允许 `RecoveryContinuity` 复位。
7. 关闭上游前发送 `session.finish`，最多等待 `250ms` 排空。
8. 单窗双语同屏消费 `source_font_size`、`target_font_size`、
   `subtitle_emphasis` 与 `bilingual_layout_order`；主次只允许字号、字重和
   文字透明度差异，字幕禁止标红。

## 后果

- 原生 macOS 透明悬浮、跨 Space 与 `80ms` 点击穿透仍属于 R10–R12，
  R09.5 只交付 Web 单窗与配置契约，不提前宣称已完成。
- 旧全量词库与多窗口 ADR 仅保留为历史记录，不得作为现行实现依据。
- 真实 DashScope 连续语音验收必须由用户使用本机 `.env` 完成；Mock 测试
  不能替代真机 `/400`、`/1007` 结论。

## 验证

- `tests/core_regression/test_glossary_priority.py`
- `tests/core_regression/test_qwen_adapter.py`
- `tests/core_regression/test_main_fastpath.py`
- `tests/unit/test_config_panel.py`
- `tests/unit/test_frontend_structure.py`
- `cd frontend && npm run build && npm test`

# ADR-005：1 至 3 个 Mac 窗口并行多语种

- **状态**：Superseded by `ADR-008-r095-single-window-wire-budget.md`
- **日期**：2026-11-22
- **决策人**：user

## 背景

单条 Qwen 3.8 实时会话只绑定一个目标语种。仅靠文本二次翻译会增加 150 至 400ms 延迟，也不能完整保留实时语音中的停顿与语气。会议现场可能需要同时给不同受众显示不同语言。

## 决策

1. 支持同时开启 1、2 或 3 个独立 Tauri `WebviewWindow`。
2. 每个窗口独立绑定音频源、源/目标语种、Mimi 14 项样式、屏幕坐标和锁定状态。
3. 同一音频源按内存引用零拷贝扇出；每个活跃窗口维护自己的 `QwenLiveTranslate38Engine` 会话。
4. 通用路由键固定为 `(window_id, audio_source, target_lang)`，不得恢复中英专用 `is_echo` 过滤。
5. 关窗即断连：关闭窗口、停用字幕或删除窗口时，立即释放对应上游 WebSocket。
6. 无活跃窗口时，音频采集不得触发任何上游 `append`。
7. 设置页和用户文档可显示静态的 2x/3x 活跃会话费用提示；该提示不得依赖 Token、`usage` 或费用计量事件。
8. 对错峰重连与 RPM 设置令牌桶保护，防三窗口同时重连打满限流。
9. 每窗口字幕必须带 `window_id` 并独立过滤，禁止串语。

## 后果

- R06 先完成可测试的 `SessionPool`，R12 再接入多窗口 UI 和真机并发。
- 三窗并发会增加云端音频输入用量，因此必须有明确 UI 提示和关闭即断连。
- 内存中共享 PCM 块的生命周期需要单独测试，禁止窗口间持有可变引用。

## 验证

- R06：`tests/unit/test_engine_abstraction.py`
- R12：`tests/core_regression/test_multi_window_lang.py`
- R12：真实三窗短测，若条件不足必须标为 `PARTIAL`

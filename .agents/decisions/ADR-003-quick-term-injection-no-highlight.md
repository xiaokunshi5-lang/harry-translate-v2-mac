# ADR-003：快捷临时词会话注入与字幕画面绝对纯净

- **状态**：Accepted
- **日期**：2026-11-22
- **决策人**：user

## 背景

会议中经常临时出现人名、公司名和专业缩写。若每次都要离开字幕视口进入设置页，会打断控台操作。Echo 风格的未译词提示如果直接渲染到字幕，会产生标红和视觉噪声，违反用户的纯净画面要求。

## 决策

1. 在 Tauri 浮窗与主控台提供 `QuickTermBar`，支持快捷呼出和 `源词=目标词` 输入。`黄仁勋=Jason Huang` 仅作示例，不写死方向。
2. 支持 `asr_fixes`、`zh->en`、`en->zh`、`target_fixes` 全方向输入和多种分隔符。
3. 本地词典必须毫秒级热重编，不因新增临时词断开当前实时翻译会话。
4. 有活跃云端会话时，通过一次增量 `corpus.phrases` 更新推送新增词；不得重复重连握手。
5. 临时词仅属于当前会话；支持单条 `promote_term` 转永久，支持会后 `clear_session` 清空。
6. 字幕画面严禁自动标红、下划线、异常徽章或未译词提示。`untranslatedWords()` 只能作为后台静默加急的优先级条件。

## 后果

- R09 必须同时实现本地消息状态、QuickTermBar 和原子持久化，不能只做设置页表单。
- 单条转永久与会后清空必须有独立测试，避免误删永久自定义词。
- R14 的旁路纠错不得重新引入视觉标红。

## 验证

- `tests/core_regression/test_glossary_engine.py`
- `tests/core_regression/test_bypass_isolation.py`
- UI 轮次必须检查字幕 DOM 和 CSS 中不存在异常标红状态。

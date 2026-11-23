# ADR-002：18,517 词全量留驻与两段式异步建树

- **状态**：Superseded by `ADR-008-r095-single-window-wire-budget.md`
- **日期**：2026-11-22
- **决策人**：user

## 背景

目标词库来自未改进版：15 个词包共 18,449 词，加永久自定义词 `glossary.json` 68 词，合计 18,517 词。旧的 `extensions/optional_packs/` 方案会把 15 个包中的部分包移出主目录并默认关闭，违背“完整复刻”的目标。同步全量建树又会阻塞冷启动。

## 决策

1. `glossary_bank/` 必须完整留驻 15 个词包，`config/glossary.json` 保留 68 条永久词，全部默认开启。
2. `00_base_emergency` 为 P2 锁定基础包，不可关闭；`01~14` 为 P3 包。
3. 四级优先级固定为 `P0 会话临时词 > P1 永久自定义词 > P2 锁定基础包 > P3 补充词库`。
4. 启动先加载 `00~08`，目标约 230ms 内让主界面可用；`09~14` 在后台线程异步编译，目标约 550ms，完成后原子替换自动机引用。
5. `/api/glossary/terms/active` 必须支持 `category=asr_fixes|terms_zh_to_en|terms_en_to_zh` 与 `limit/offset/q` 分页，禁止一次返回 4.25MB 全表。
6. `asr_fixes`、`terms_zh_to_en`、`terms_en_to_zh` 三张表物理隔离；译文方向不得反转。
7. 匹配使用单次扫描 `[start, end)` 区间锁，防止级联二次篡改；英文词边界使用 ASCII 判定。
8. 云端注入上限从 `SAFE_CAP=48` 提升为 `900`，不得静默回退扫描用户已关闭的词包。
9. macOS 写入使用 `NamedTemporaryFile`、`fcntl.flock` 与 `os.replace`，并保留指数退避重试。Windows 文件锁不在本项目范围。
10. 废弃 `extensions/optional_packs/`，不创建该目录。

## 后果

- 常驻内存会高于 V1，设计预算约为 Trie 46MB 量级，必须通过 `__slots__` 和后端分页控制。
- R08 必须在业务测试中证明词数、默认开关、两段式时序和三类分页。
- R09 必须证明方向隔离、区间锁、ASCII 边界、QuickTermBar 和 `900` 上限。
- 后备策略只能是 `00~08` 暂时兜底，不能永久关闭 `09~14`。

## 验证

- `tests/core_regression/test_glossary_priority.py`
- `tests/core_regression/test_glossary_engine.py`
- R17 冷启动压测必须覆盖 18,517 词而不阻塞首字。

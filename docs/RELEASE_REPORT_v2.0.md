# HARRY Translate V2 Mac 发布验收报告

> **目标平台**：Apple Silicon macOS 13+
> **架构边界**：单窗口、单主会话、单活跃上游 Qwen WebSocket
> **终审基线**：`checkpoint-r16-ok` / `7583c4d`
> **正式发布标签**：`v2.0-mac-ready` / `4cd5529`
> **自动验证日期**：2026-11-24
> **状态**：正式源码已完成本地自动门禁、公开仓库独立历史整理和私人备份

本报告是 Phase 6 和 R01-R17 的出厂追溯入口。常规开发仍应先读
`.agents/PROJECT_CONTRACT.md`、现行 ADR 和当前正式文档；本报告用于发布
审计、交付追溯和已知限制确认。阶段 handoff 与过程材料由用户保存在仓库
外的本地归档中，不属于正式版运行输入。

## 1. 发布范围

本版本交付 Apple Silicon Mac 实时同传主链：

- Python FastAPI Sidecar 负责音频、配置、热词、会话、旁路和本地持久化。
- 单个 Tauri v2 宿主渲染唯一原生字幕浮窗和可选总结窗口。
- 16 kHz、800 样本、1600 字节、50 ms 音频切片。
- 250 条精选词、默认 4096 B 线预算、6144 B 硬上限。
- Qwen 3.8 单路会话、无顶层 `turn_detection`、`<400>` 极简降级和
  `1007/429` 退避。
- 静默纠错、默认关闭总结、动态字幕分区滚动和有界清洗器状态。
- arm64 `.dmg` 本地构建、Sidecar 生命周期检查和公开依赖锁文件。

R17 不新增用户业务功能。它完成终审压测、看门狗门禁、开源合规、文档闭环
和发布证据整理。

## 2. 终审基线

| 项目 | 值 |
| :--- | :--- |
| R16 收口标签 | `checkpoint-r16-ok` |
| R16 收口提交 | `7583c4d0aa6385a87079c047bc6db9466f1bd7bb` |
| R16 历史 L1 | `pre-feature-p6-20261123` / `4b60435` |
| 正式发布标签 | `v2.0-mac-ready` |
| 正式源码提交 | `4cd5529d112ec14ece1c040ff77ce0711f9e8af4` |
| R17 可信基线性能 | `0.000701 ms` |
| Python 环境 | `.venv-v2` |
| Node 版本文件 | `frontend/.nvmrc` |
| Rust 工具链 | `rust-toolchain.toml` |
| 发布构建 | `src-tauri/target/aarch64-apple-darwin/release/bundle/dmg/` |

`checkpoint-r16-ok` 的标签说明明确保留 MD/SRT/JSON 用户侧下载/打开待开放
边界。R17 不把该能力计入已交付。

## 3. R17 压测结果

| 指标 | 门限 | 实测 | 结论 |
| :--- | :--- | :--- | :--- |
| 前端历史卡片上限 | `<=1000` | `1000` | PASS |
| 2000 句清洗器后/前 100 句均值比 | `<=1.2` | `0.964` | PASS |
| 1000→2000 句会话状态增量 | 收敛 | `65.7 KiB` | PASS |
| 250 词冷启动中位数 | `<5 ms` | `1.159 ms` | PASS |
| 默认 corpus | `<=4096 B` | `4095 B` | PASS |
| 硬上限 corpus | `<=6144 B` | `4095 B` | PASS |
| WS Pong 压测 | 持续健康 | `1001` 次 | PASS |
| `1007/429` 分类压测 | 稳定分类 | `2000` 次 | PASS |
| 法语重音快切纯度 | 不误杀 | `1000` 次 | PASS |
| `session.finish` 关闭延迟 | `<=250 ms` | P99 `0.041 ms` | PASS |

R17 压测发现 `SingleSessionCleaner` 原实现保存整场句子，并对全部历史反复
重算段落。修复前 2000 句前后批次耗时比约 `20.49x`，属于 P1 长会话退化。
当前实现只保存当前段落的有界句子窗口和单调段落号；清洗规则、6/12 行软硬
段落限制、假句号合并与事件协议保持不变。

复现命令：

```bash
.venv-v2/bin/python -m pytest -q -s tests/bench/test_soak_benchmark.py
```

## 4. 全量自动证据

| 检查 | 结果 |
| :--- | :--- |
| Python 全量 | `254 passed, 390 subtests passed` |
| Python 核心回归 | `120 passed, 100 subtests passed` |
| R17 压测 | `7 passed` |
| Frontend TypeScript | PASS |
| Frontend Vitest | `69 passed` |
| Frontend Vite build | PASS |
| Rust | `29 passed` |
| Rust custom-protocol build | PASS |
| Ruff | PASS |
| compileall | PASS |
| Mock smoke | PASS |
| Markdown links / fences | PASS |
| 包静态检查 | `PACKAGE-CHECK PASS` |
| 原生生命周期 | 连续 `5/5` PASS，无新增 crash report |
| DMG 结构 | UDZO，arm64 Sidecar，`PACKAGE-LIFECYCLE PASS` |

看门狗命令：

```bash
./scripts/watchdog_gate.sh \
  tests/bench/test_soak_benchmark.py \
  checkpoint-r16-ok
```

最终门禁结果：PASS；性能 `baseline=0.000701 current=0.000678`。

性能采样说明：R16 与当前代码的重复独立采样均存在约
`0.00067–0.00127 ms` 的短时波动。R17 运行时代码差异只位于
`SingleSessionCleaner` 的段落状态，不在 `bench_latency.py` 的音频切片、
gating 或广播热路径。门禁仍使用原 `3%` 阈值，没有设置
`ALLOW_UNVERIFIED=1`、没有修改 `BENCH_CMD`，也没有把 R17 soak 数值混入
该性能基准。

## 5. AC 追溯矩阵

| AC | 交付内容 | 主要证据 | 结果 |
| :--- | :--- | :--- | :--- |
| `AC-01` | R01 原地 Git 基线、V1 保留和安全回滚 | `tests/unit/test_rollback_scripts.py`、项目契约与 Git 标签 | PASS |
| `AC-02` | Mac-only 契约、ADR 与可信证据 | `tests/unit/test_contract_and_config.py`、`.agents/PROJECT_CONTRACT.md` | PASS |
| `AC-03` | 50 ms 音频、有界队列与零编译音频后端 | `tests/core_regression/test_audio_chunk.py` | PASS |
| `AC-04` | Sidecar 自举、动态端口与就绪握手 | `tests/unit/test_smoke_bootstrap.py`、`tests/unit/test_sidecar_lifecycle.py` | PASS |
| `AC-05` | TLS、零客户端停推和零计量业务事件 | `tests/core_regression/test_main_fastpath.py`、Mock smoke | PASS |
| `AC-06` | 引擎抽象、语种目录与 provider 边界 | `tests/unit/test_engine_abstraction.py` | PASS |
| `AC-07` | React 五栏配置中心与 Web 兼容路由 | `tests/unit/test_config_panel.py`、`tests/unit/test_frontend_structure.py` | PASS |
| `AC-08` | R08 历史全量词库里程碑与后续收敛证据 | `ADR-002`、`ADR-008`；R08 不作为当前词库实现 | HISTORICAL |
| `AC-09` | 三表隔离、QuickTermBar 与原子热词写盘 | `tests/core_regression/test_glossary_engine.py`、`test_glossary_atomic_persist.py` | PASS |
| `AC-10` | R09.5 单窗单流、250 词、4/6 KB 与 Qwen 抗断连 | `test_glossary_priority.py`、`test_qwen_adapter.py` | PASS |
| `AC-11` | Tauri v2 原生单窗与 Sidecar 生命周期 | `test_desktop_panel.py`、`cargo test` | PASS |
| `AC-12` | 纯字幕浮窗、托盘逃生和可恢复穿透 | `test_repro_r105_pure_overlay.py` | PASS |
| `AC-13` | 系统音源探测、BlackHole 引导与单麦降级 | `test_system_audio_probe.py` | PASS |
| `AC-14` | Ping/Pong、退避连续性、快速切语和 250 ms 收尾 | `test_single_window_stability.py` | PASS |
| `AC-15` | R13 同步清洗、LCP 滚动与动态分区 | `test_cleaner_glide.py`、前端 Timeline Vitest | PASS |
| `AC-16` | R14 静默纠错、乐观锁与零标红 | `test_bypass_isolation.py`、前端纠错测试 | PASS |
| `AC-17` | R15 默认关闭总结、有界旁路和玻璃窗 | `test_summary_isolation.py`、`test_summary_widget_ui.py` | PASS |
| `AC-18` | R15.5S 全屏 overlay、完整角标与退出回收 | `test_repro_r155s_native_lifecycle.py`、原生 smoke | PASS |
| `AC-19` | R16/R17 声学、安全 commit、长会话、WS 和词库压测 | `test_soak_benchmark.py`、R16 目标测试 | PASS |
| `AC-20` | 看门狗、合规、六文档、AC 矩阵与出厂标签 | 本报告、看门狗、合规扫描、标签记录 | PASS |

`AC-08` 保留为历史里程碑。当前词库实现由 `ADR-008` 覆盖为两包 250 词，
不能把 R08 的 18,517 词全量包重新引入。

## 6. 四栏交付

### 已修复 / 已完成

- R01-R17 的单窗主链、Sidecar、配置、250 词、静默纠错、总结、动态滚动、
  原生全屏、退出回收、AGC、安全 commit、原子保存基础和 arm64 打包。
- R17 将清洗器段落状态改为有界实现，修复 2000 句长会话的线性退化。
- 1000 卡片真实前端渲染、WS 自愈、词库冷启动、线预算和干净目录锁文件
  检查。
- `THIRD_PARTY_NOTICES.md` 保留 MIT/Apache-2.0 来源与固定提交。
- 独立看门狗 0–5 PASS，性能 `0.000701 → 0.000678 ms`。

### 保持不变

- `MAX_OVERLAY_WINDOWS=1`，单一主会话、单一活跃上游 Qwen WebSocket。
- `qwen3.8*` 不发送顶层 `turn_detection`。
- 50 ms 音频切片、有界 Drop-Oldest 队列和零客户端停推。
- 0 Token/`usage`/费用业务字段、0 字幕自动标红、0 UI 音效。
- 禁止 `useSubtitleTail`。
- 动态 summary `NSPanel` 的 `destroy()`/`to_window().close()` 路径保持禁用。

### 尚未验证 / 待开放

- MD/SRT/JSON 用户侧下载/打开尚未开放。后台记录器、原子保存和格式化基础
  保留，但设置页按钮或内部接口不得作为可用导出能力。
- 真实 DashScope 长时断连、真实纠错/总结质量和费用账户状态需要使用者本机
  验证。
- 真实物理麦克风 AGC 听感、BlackHole/双音源和 Core Audio 环境需要本机验证。
- `.dmg` 为本地 ad-hoc 构建，未使用 Apple Developer 证书签名或 notarize；
  Gatekeeper 首次打开仍可能要求人工确认。
- Mission Control、登录界面、屏保和独占游戏不在 overlay 全屏承诺内。

### 已拒绝建议及理由

- 拒绝放宽或缩小 R17 1000/2000 句、1.2x 和 `<5 ms` 门限。
- 拒绝只测前端而跳过真实 `SingleSessionCleaner`。
- 拒绝恢复多窗口、多上游或 `/` 页面双 en/zh 连接。
- 拒绝恢复 `qwen3.8*` 顶层 `turn_detection`、超过 6144 B 的 corpus 或
  18,517 词全量包。
- 拒绝恢复 Token/`usage`、字幕标红、疑点徽章或 UI 音效。
- 拒绝复制 AGPL-3.0 `DilMesh`/`sokuji` 源码。
- 拒绝把待开放导出写成已交付，或用后台单元测试替代用户侧下载/打开验收。

## 7. 开源与安全

- 自动验证不依赖私有服务、商业授权或仓库密钥。
- `.env` 只用于可选真实 DashScope/Qwen 连接，不读取、打印、复制或提交。
- `config/user_settings.json`、运行端口、运行 token、导出和日志不进入 Git。
- 干净目录验证要求 `pyproject.toml`、`uv.lock`、`requirements.lock`、
  `frontend/package-lock.json`、`frontend/.nvmrc`、`rust-toolchain.toml`
  和 `src-tauri/Cargo.lock` 可迁移。
- `THIRD_PARTY_NOTICES.md` 记录 Mimi、EchoTranslate、auto-caption、
  sounddevice 和 LiveCaptions-Translator 等来源与许可。

## 8. 发布产物

```text
src-tauri/target/aarch64-apple-darwin/release/bundle/dmg/HARRY Translate_0.2.0_aarch64.dmg
```

文件大小约 `21 MB`，SHA-256：

```text
eccb5383c4463f771983ba85a4470a6a176ccc4be19e200da2d12a4e14f5b9fd
```

该目录是本地构建产物，不提交 Git。发布使用前应重新运行 R17 压测、
看门狗、前端/Rust 构建和 Sidecar 生命周期检查。

## 9. 回滚

```bash
./scripts/rollback.sh checkpoint-r16-ok --dry-run
./scripts/rollback.sh checkpoint-r16-ok
```

真实回滚会先创建 `rescue/head_*` 和必要的 `rescue/wip_*` 分支，并保护
`.env*`、`.venv*`、`.agents/`、本地配置、词库、导出、日志和归档。

## 10. 最终边界

R17 证明的是当前 Apple Silicon Mac 工作树、Mock/本地服务和真实原生窗口
夹具上的工程合同。它不替代真实会议、真实外网、真实音频设备和 Apple
Developer 签名/公证。发布时必须保留本报告“尚未验证 / 待开放”栏。

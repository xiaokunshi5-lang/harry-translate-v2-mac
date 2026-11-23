# ADR-004：Tauri v2、Mimi 前端与 Python Sidecar 的 Mac-only 组合

- **状态**：Accepted
- **日期**：2026-11-22
- **决策人**：user

## 背景

V1 是单一 HTML 加 FastAPI 应用，后续需要 Mac 原生透明悬浮窗、跨 Space、点击穿透、多窗口和可打包的本地运行时。手写原生壳成本高且难以对齐 Mimi 的交互质量。另一方面，原计划包含 Windows Tauri 配置与打包文件，超出了用户本次范围。

## 决策

1. 采用 Tauri v2 + React 19 + TypeScript + Tailwind + Zustand 薄壳，复用 MIT `yuxino/Mimi` 的 Mac 悬浮窗与 14 项界面资产。
2. Python 业务内核使用 FastAPI WebSocket Sidecar，保留 `/`、`/en`、`/zh`、`/split` Web 兼容路由，并新增 `/settings`、`/hud`、`/ws/window/{window_id}`。
3. Sidecar 支持动态端口、stdout 单行 JSON 就绪握手、`.agents/runtime_port` 和父子进程回收。
4. 只构建 Apple Silicon arm64 `.dmg`。不创建 `tauri.windows.conf.json`、NSIS、WebView2 引导或 Windows 原生源码。
5. 复用源固定如下：

| 项目 | 许可证 | 固定 commit | 使用边界 |
| :--- | :--- | :--- | :--- |
| `yuxino/Mimi` | MIT | `bce7164fcea4591b912050c298c9b72bdbeb6f61` (`v1.7.6`) | Tauri Mac 壳、React 组件、14 项交互；不引入 `useSubtitleTail.ts` |
| `HiMeditator/auto-caption` | MIT | `927a393561bf5fd33a6f3a1803a659558cf93b4d` | 仅参考 Sidecar 生命周期与握手协议 |
| `phuc-nt/my-translator` | MIT | `3495f99e3c524f43a64fd93307a0140c1a2b25c8` | 参考多会话路由与连接池 |
| `SakiRinn/LiveCaptions-Translator` | Apache-2.0 | `a6fee12757b15edbeef7c60f8895e0be694801e1` | 参考 `ResizeForOnlyMode` 防高度跳变 |
| `Samoyed123-yed/echo-translate` | MIT | `fc8d9a2195903cef780c8a295183b21a3561db12` | 参考清洗、静默旁路、乐观锁与原子持久化 |

6. 不复制 `hayamimi` 源码，因为其许可元数据不明确。
7. 不复制 AGPL-3.0 项目 `DilMesh` 或 `sokuji` 的任何源码。
8. 所有临时拉取放入仓库内忽略目录 `.tmp/`；只把合法复用的最小代码、版权文本和许可原文放入受控目录。
9. 后续分别以 `uv.lock`、Node 锁文件、`Cargo.lock` 和 `rust-toolchain.toml` 锁定三端依赖。

## 后果

- 开发需要 Node、Rust 和 macOS 打包工具，但终端用户只接触 `.dmg`。
- Mimi 新 HEAD 不得自动升级；任何版本变化必须建立新的 ADR 并重跑界面与原生测试。
- Windows 相关原文件即使出现在上游，也不允许复制进本项目。

## 验证

- R04 验证 Sidecar 握手。
- R07 验证 React 配置中心与 Web 路由。
- R10 验证 Tauri Mac 原生窗、线程回收和 `/hud` 兜底。
- R16 验证 arm64 `.dmg` 冷启动。

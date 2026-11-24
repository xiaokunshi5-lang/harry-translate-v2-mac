# HARRY Translate V2 Mac 使用与验收教程

本教程用于验证正式版主链。自动门禁和 Mock 可以验证协议、窗口、状态机
与本地服务；真实 DashScope 翻译质量、真实麦克风听感和真实系统回环设备
必须由使用者在本机单独验收。

R18-01 至 R18-04 已于 2026-11-24 由用户全部验收通过。下面第 1 节是
从 DMG 开始的复验入口；需要检查底层实现时，再按第 2 节准备源码环境。

## 1. 从 DMG 复验 R18

按 [INSTALL.md](./INSTALL.md) 下载、校验并安装 `v2.0-r18-ready`。应用内部
版本仍为 `0.2.0`。

### 安静启动和真实开关

1. 启动应用，先不要开启实时翻译。确认只出现设置窗口，没有自动采音、
   云端连接或字幕推流。
2. 确认顶栏只有一个实时翻译滑钮。开启后显示真实运行状态，关闭后停止
   音频和云端会话；界面不能仅改变颜色而后台继续运行。
3. 没有 API Key 时，滑钮应禁用并说明原因。

### 设置窗口和退出

1. 关闭设置窗口，再分别点击 Dock 图标、在 Finder 中重新打开应用、使用
   菜单“打开设置窗口”或按 `Command + ,`。每条路径都应唤回同一个设置窗口。
2. 关闭设置窗口不应停止已经开启的翻译。
3. 从设置窗口、应用菜单、`Command + Q` 或菜单栏托盘退出应用。确认字幕、
   后台 Sidecar 和音频采集都结束，没有残留 HARRY 进程。

### DMG 和系统提示

1. 核对 Release 页面列出的文件大小和 SHA-256，并运行 `hdiutil verify`。
2. 当前包为 ad-hoc 签名、未公证；带 quarantine 时仍可能提示“已损坏”。
   只有校验通过后，才可按 INSTALL 的说明对单个 HARRY 应用清除 quarantine。
3. 不要关闭系统全局 Gatekeeper。Developer ID 公证和干净 Mac 无提示启动
   不属于本次已验收结论。

本节通过后，日常功能可按 [USER_GUIDE.md](./USER_GUIDE.md) 使用。后续章节
用于完整工程复验。

## 2. 验收前准备

先完成 [INSTALL.md](./INSTALL.md) 中的开发环境安装，然后检查：

```bash
git status --short --branch
stat -f '%Sp %N' .env
.venv-v2/bin/python --version
test -d frontend/node_modules
```

预期：

- 工作区干净；
- `.env` 权限为 `0600`；
- Python 为 3.12.x；
- `frontend/node_modules` 已按锁文件安装。

不要输出 `.env` 内容。

## 3. 构建前端

```bash
cd frontend
npm ci
npm run typecheck
npm test
npm run build
cd ..
```

必须存在 `frontend/dist/index.html` 和 `frontend/dist/assets/`。

## 4. 启动与进程边界

原生桌面应用：

```bash
./启动桌面悬浮窗.command
```

Web 控台：

```bash
./启动同传系统.command
```

检查：

```bash
cat .agents/runtime_port
curl --fail --silent "http://127.0.0.1:$(cat .agents/runtime_port)/api/health"
```

预期：

- 只有一个 `harry-translate-mac` Tauri 宿主；
- 只有一个主 Python Sidecar；
- 冷启动只显示原生设置窗口，不自动打开浏览器，也不显示字幕浮窗；
- 未开启实时翻译时，麦克风指示灯不亮；
- 菜单栏出现「同传」托盘；
- 重复双击启动入口不会创建第二个宿主；
- 关闭终端或退出后不残留桌面浮窗。

## 5. 五栏配置中心

正常启动后直接在原生设置窗口操作，不需要先打开浏览器。下面的地址只用于
辅助大屏或开发排查：

```text
http://127.0.0.1:<port>/settings
```

### 5.1 模型与服务

- 检查 Qwen 模型、API Key 掩码和 Workspace。
- 无 Key 时应显示 `needs_setup=true`，不能显示云端已就绪。
- “连通性测验”只做短时 TLS 可达性检查，不发送音频。

### 5.2 声音与语言

- 选择麦克风、系统声音或双音源。
- 切换源语和目标语。
- 没有 BlackHole 时选择系统声音，应回退麦克风并显示
  `loopback_missing` 引导。

### 5.3 字幕与外观

- 切换原文、译文和单窗双语。
- 分别调整原文字号、译文字号、主次、上下顺序、行距、透明度、颜色和对齐。
- 检查顶部实时预览跟随设置变化。
- 检查字幕区域无自动标红、下划线或异常徽章。

### 5.4 热词与总结

- 查看 250 条精选词、4096 B 默认线预算和 6144 B 硬上限。
- 使用 `QuickTermBar` 注入会话词。
- 将单条临时词升格为永久词，并确认配置文件原子写入。
- 清空本场临时词，不修改磁盘永久词。
- 静默纠错和总结可独立关闭；关闭时不得产生任务、请求或总结窗口。

### 5.5 通用与诊断

- 检查端口、Python 版本、TLS 状态、音频后端和掩码 Key。
- 页面不得出现 Token、`usage`、费用或账单字段。

## 6. 原生字幕浮窗

### 6.1 基础显示

1. 浮窗内部只能有字幕，不能有按钮、链接或状态胶囊。
2. 标准态为纯黑半透明背景，说话和停顿时不闪烁、不变绿、不模糊。
3. 可拖动和缩放。
4. 纯译文、纯原文、单窗双语和沉浸态切换时，底边锚点保持稳定。
5. 历史卡片滚动后，新字幕不得强制把阅读位置拉回底部。

### 6.2 穿透恢复

1. 从托盘开启沉浸模式。
2. 鼠标应能操作下层应用。
3. 从托盘或原生设置窗口关闭沉浸模式。
4. 锁定穿透也使用同样路径恢复。

不得依赖穿透窗内悬停来恢复。

### 6.3 原生全屏

依次验证：

1. 先显示字幕浮窗，再进入 QuickTime、Safari 视频、Keynote 或
   PowerPoint 的 macOS 全屏 Space。
2. 先进入全屏，再唤出字幕浮窗。
3. 全屏中隐藏字幕，再重新显示。
4. 在普通 Space 和全屏 Space 间切换。

预期：

- 字幕始终显示在全屏应用上方；
- 不抢键盘焦点；
- 不把全屏应用拉回桌面；
- 不创建第二个字幕浮窗。

Mission Control、登录界面、屏保和独占游戏不在本项承诺内。

## 7. 总结窗口

总结默认关闭。首次启动应满足：

- 没有总结图标；
- 没有总结 DOM；
- 没有总结 WebView 窗口；
- 没有 `/api/summary` 空转请求。

开启总结后：

1. 等待达到总结门控，或使用手动刷新。
2. 检查 4-5 级重点置顶、1-3 级折叠。
3. 检查 48x48 icon 的无角标、个位数和 `9+` 三种状态均无裁切。
4. 关闭并重开总结窗口至少 3 次，宿主不得崩溃。
5. 关闭总结后，图标、面板和后续请求应全部停止。

总结窗口不承诺进入其他应用的全屏 Space。字幕浮窗的全屏能力不受影响。

## 8. 真实音频

### 8.1 麦克风

1. 正常距离说话，确认字幕即时出现。
2. 弱音应温和增强，底噪不得被无差别放大。
3. 突然高声不得持续削波。
4. 2.4 秒停顿或 3.8 秒连续语音后允许安全 commit。
5. 字幕不得因为平滑动画人为延后 100-400 ms。

### 8.2 系统声音

1. 在 macOS「音频 MIDI 设置」创建含 BlackHole 2ch 的多输出设备。
2. 将系统声音输出切到该设备。
3. 在设置中选择系统声音。
4. 播放会议或视频，确认回环帧直通，无重复 AGC。

### 8.3 快速切语

连续切换 `zh->en`、`zh->fr`、`en->zh`：

- 旧会话先收尾；
- 旧语种残句不进入新目标语；
- 法语重音、日语、韩语、数字和技术缩写不被误杀；
- 状态胶囊显示真实 connected、reconnecting 或 degraded 状态。

## 9. 热词与原子保存

1. 按 `Command + Shift + G` 打开快捷词条。
2. 输入一条会话词并提交。
3. 检查词条在 5 ms 量级热生效，字幕区域仍无标红。
4. 在设置第 4 栏将单条词升格为永久词。
5. 检查 `config/glossary.json` 仍是完整 UTF-8 JSON，且无残留 `.tmp`。
6. 清空本场临时词，确认永久词不受影响。

## 10. 会话与导出边界

当前版本保留以下后台基础：

- 单会话记录器；
- `clientSavedAt` 单调保护；
- 原子 JSON 保存；
- MD/SRT/JSON 格式化函数。

但是 **MD/SRT/JSON 用户侧下载/打开尚未开放**。设置页中的按钮或内部接口
不能作为已交付能力。不要删除 `app/transcript_exporter.py`，它是后续修复
和审计的边界。

## 11. 自动回归

```bash
(cd frontend && npm run typecheck && npm test && npm run build)
cargo test --offline --manifest-path src-tauri/Cargo.toml
cargo build --offline --features custom-protocol --manifest-path src-tauri/Cargo.toml
.venv-v2/bin/python -m pytest -q tests/
.venv-v2/bin/python -m pytest -q tests/core_regression/
.venv-v2/bin/python scripts/smoke_e2e.py --mock
.venv-v2/bin/python -m pytest -q -s tests/bench/test_soak_benchmark.py
.venv-v2/bin/python -m ruff check app scripts tests switch_key.py
.venv-v2/bin/python -m compileall -q app server.py switch_key.py scripts tests
./tests/check_markdown_links.sh
```

原生生命周期：

```bash
.venv-v2/bin/python -m pytest -q tests/unit/test_repro_r155s_native_lifecycle.py
for i in 1 2 3 4 5; do
  .venv-v2/bin/python tests/smoke/native_overlay_lifecycle.py
done
```

## 12. 退出

正常退出：

1. 在原生设置窗口点击“退出应用”；
2. 使用 `Command + Q` 或 `HARRY Translate > 退出 HARRY Translate`；
3. 从 `harry-tray` 选择“退出 HARRY Translate”。

检查：

```bash
ps -axo pid=,comm= | rg 'harry-translate-mac|harry-sidecar' || true
```

预期无输出。不要使用 `killall python` 或宽泛 `pkill`。

## 13. 已知限制

- MD/SRT/JSON 用户侧下载/打开待开放。
- 真实 DashScope 长时断连需要本机联网复验。
- 真实纠错和总结质量需要本机模型复验。
- 真实 AGC 听感和系统回环设备需要人工验收。
- 总结窗口不加入其他应用的全屏 Space。
- 当前 `.dmg` 仍是 ad-hoc 签名，未完成 Apple Developer ID 签名和
  notarization；干净 Mac 双击零提示启动仍待证书环境验证。

## 14. 应急回退

V1 回退入口：

```bash
./启动同传系统.command --legacy
```

等价：

```bash
./start.sh
```

V1 只用于应急恢复和对照，不属于 V2 自动验收，也不能证明单窗原生浮窗、
全屏层级、Qwen 3.8 协议或 V2 保存边界。

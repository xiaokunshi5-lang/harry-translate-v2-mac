# HARRY Translate V2 Mac 使用与验收教程

本教程用于验证正式版主链。自动门禁和 Mock 可以验证协议、窗口、状态机
与本地服务；真实 DashScope 翻译质量、真实麦克风听感和真实系统回环设备
必须由使用者在本机单独验收。

## 1. 验收前准备

先完成 [INSTALL.md](./INSTALL.md) 中的环境安装，然后检查：

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

## 2. 构建前端

```bash
cd frontend
npm ci
npm run typecheck
npm test
npm run build
cd ..
```

必须存在 `frontend/dist/index.html` 和 `frontend/dist/assets/`。

## 3. 启动与进程边界

原生桌面浮窗：

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
- 菜单栏出现「同传」托盘；
- 重复双击启动入口不会创建第二个宿主；
- 关闭终端或退出后不残留桌面浮窗。

## 4. 五栏配置中心

打开：

```text
http://127.0.0.1:<port>/settings
```

### 4.1 模型与服务

- 检查 Qwen 模型、API Key 掩码和 Workspace。
- 无 Key 时应显示 `needs_setup=true`，不能显示云端已就绪。
- “连通性测验”只做短时 TLS 可达性检查，不发送音频。

### 4.2 声音与语言

- 选择麦克风、系统声音或双音源。
- 切换源语和目标语。
- 没有 BlackHole 时选择系统声音，应回退麦克风并显示
  `loopback_missing` 引导。

### 4.3 字幕与外观

- 切换原文、译文和单窗双语。
- 分别调整原文字号、译文字号、主次、上下顺序、行距、透明度、颜色和对齐。
- 检查顶部实时预览跟随设置变化。
- 检查字幕区域无自动标红、下划线或异常徽章。

### 4.4 热词与总结

- 查看 250 条精选词、4096 B 默认线预算和 6144 B 硬上限。
- 使用 `QuickTermBar` 注入会话词。
- 将单条临时词升格为永久词，并确认配置文件原子写入。
- 清空本场临时词，不修改磁盘永久词。
- 静默纠错和总结可独立关闭；关闭时不得产生任务、请求或总结窗口。

### 4.5 通用与诊断

- 检查端口、Python 版本、TLS 状态、音频后端和掩码 Key。
- 页面不得出现 Token、`usage`、费用或账单字段。

## 5. 原生字幕浮窗

### 5.1 基础显示

1. 浮窗内部只能有字幕，不能有按钮、链接或状态胶囊。
2. 标准态为纯黑半透明背景，说话和停顿时不闪烁、不变绿、不模糊。
3. 可拖动和缩放。
4. 纯译文、纯原文、单窗双语和沉浸态切换时，底边锚点保持稳定。
5. 历史卡片滚动后，新字幕不得强制把阅读位置拉回底部。

### 5.2 穿透恢复

1. 从托盘开启沉浸模式。
2. 鼠标应能操作下层应用。
3. 从托盘或浏览器配置页关闭沉浸模式。
4. 锁定穿透也使用同样路径恢复。

不得依赖穿透窗内悬停来恢复。

### 5.3 原生全屏

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

## 6. 总结窗口

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

## 7. 真实音频

### 7.1 麦克风

1. 正常距离说话，确认字幕即时出现。
2. 弱音应温和增强，底噪不得被无差别放大。
3. 突然高声不得持续削波。
4. 2.4 秒停顿或 3.8 秒连续语音后允许安全 commit。
5. 字幕不得因为平滑动画人为延后 100-400 ms。

### 7.2 系统声音

1. 在 macOS「音频 MIDI 设置」创建含 BlackHole 2ch 的多输出设备。
2. 将系统声音输出切到该设备。
3. 在设置中选择系统声音。
4. 播放会议或视频，确认回环帧直通，无重复 AGC。

### 7.3 快速切语

连续切换 `zh->en`、`zh->fr`、`en->zh`：

- 旧会话先收尾；
- 旧语种残句不进入新目标语；
- 法语重音、日语、韩语、数字和技术缩写不被误杀；
- 状态胶囊显示真实 connected、reconnecting 或 degraded 状态。

## 8. 热词与原子保存

1. 按 `Command + Shift + G` 打开快捷词条。
2. 输入一条会话词并提交。
3. 检查词条在 5 ms 量级热生效，字幕区域仍无标红。
4. 在设置第 4 栏将单条词升格为永久词。
5. 检查 `config/glossary.json` 仍是完整 UTF-8 JSON，且无残留 `.tmp`。
6. 清空本场临时词，确认永久词不受影响。

## 9. 会话与导出边界

当前版本保留以下后台基础：

- 单会话记录器；
- `clientSavedAt` 单调保护；
- 原子 JSON 保存；
- MD/SRT/JSON 格式化函数。

但是 **MD/SRT/JSON 用户侧下载/打开尚未开放**。设置页中的按钮或内部接口
不能作为已交付能力。不要删除 `app/transcript_exporter.py`，它是后续修复
和审计的边界。

## 10. 自动回归

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

## 11. 退出

正常退出：

1. 从 `harry-tray` 选择“退出悬浮窗”；或
2. 关闭启动器终端；或
3. 正常结束后端进程。

检查：

```bash
pgrep -fl 'harry-translate-mac|fullscreen-fixture'
```

预期无输出。不要使用 `killall python` 或宽泛 `pkill`。

## 12. 已知限制

- MD/SRT/JSON 用户侧下载/打开待开放。
- 真实 DashScope 长时断连需要本机联网复验。
- 真实纠错和总结质量需要本机模型复验。
- 真实 AGC 听感和系统回环设备需要人工验收。
- 总结窗口不加入其他应用的全屏 Space。
- `.dmg` 未完成 Apple Developer 签名和 notarization。

## 13. 应急回退

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

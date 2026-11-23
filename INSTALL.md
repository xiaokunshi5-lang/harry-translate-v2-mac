# HARRY Translate V2 Mac 安装与构建

本文档适用于 Apple Silicon Mac、macOS 13 及以上。项目只提供 arm64
桌面构建，不包含其他平台安装步骤。

## 1. 获取正式版

公开正式版：

```bash
git clone https://github.com/xiaokunshi5-lang/harry-translate-v2-mac.git
cd harry-translate-v2-mac
```

也可以直接使用桌面的干净发布目录。不要在旧归档目录中安装或构建。

## 2. 前置环境

需要：

- Apple Silicon Mac；
- macOS 13 或更高；
- Git；
- Python 3.12.x；
- `uv`；
- Node.js 24.x 与 npm 11.x；
- Rust 工具链（由 `rust-toolchain.toml` 固定）；
- Xcode Command Line Tools；
- 麦克风；系统声音模式还需要 BlackHole 2ch 或同类回环输入设备。

检查：

```bash
uname -m
sw_vers -productVersion
git --version
uv --version
uv run --python 3.12 python --version
node --version
npm --version
rustc --version
xcode-select -p
```

`uname -m` 必须是 `arm64`，Python 必须是 3.12.x。

如缺少 Xcode Command Line Tools：

```bash
xcode-select --install
```

## 3. Python 环境

从锁文件安装：

```bash
UV_PROJECT_ENVIRONMENT=.venv-v2 uv sync --frozen --python 3.12
.venv-v2/bin/python --version
uv lock --check
```

也可以只准备环境、不启动应用：

```bash
HARRY_BOOTSTRAP_PREPARE_ONLY=1 ./启动同传系统.command
```

启动器行为：

1. 使用独立 `.venv-v2`，不修改 V1 `.venv`。
2. 优先执行 `uv sync --frozen`。
3. 在无 `uv` 时使用 Python 3.12 venv 和带哈希的
   `requirements.lock`。
4. 官方 PyPI 失败时可回退公开镜像。
5. 缺少 `.env` 时从 `.env.example` 创建并设为 `0600`。
6. 已有 `.env` 不会被覆盖。

`requirements.txt` 只属于 `legacy/v1/` 应急回退，不用于 V2 初始化。

## 4. 本地密钥

真实 Key 只放在本机 `.env`：

```bash
cp .env.example .env
chmod 600 .env
```

只检查权限，不打印内容：

```bash
stat -f '%Sp %N' .env
```

预期：

```text
-rw------- .env
```

规则：

- `.env` 不提交、不截图、不复制到聊天或日志。
- `.env.example` 只有占位值。
- 私有 MaaS 的实例专用主机名只允许保存在本机 `.env`。
- 没有 Key 仍可执行 Mock、前端、Rust、原生窗口和本地 HTTP/WS 验证。
- Mock 结果不能替代真实 DashScope 翻译结论。

## 5. 前端依赖与构建

```bash
cd frontend
npm ci
npm run typecheck
npm test
npm run build
npm audit
cd ..
```

结果：

- `frontend/node_modules/` 按 `package-lock.json` 安装。
- `frontend/dist/` 包含可由 FastAPI 托管的正式前端。
- TypeScript、Vitest、Vite 和依赖审计应全部通过。

`frontend/dist/` 不提交 Git，但本地运行时需要先生成。

## 6. Rust 与 Tauri

```bash
cargo test --offline --manifest-path src-tauri/Cargo.toml
cargo build --offline --features custom-protocol --manifest-path src-tauri/Cargo.toml
```

正式原生产物由 Tauri CLI 构建。若本机没有安装全局 Tauri CLI，打包脚本
会使用与通知文件一致的公开 `@tauri-apps/cli` 版本。

## 7. 启动

原生桌面浮窗：

```bash
./启动桌面悬浮窗.command
```

不自动打开浏览器配置页：

```bash
./启动桌面悬浮窗.command --no-open-settings
```

Web 控台：

```bash
./启动同传系统.command
```

实际端口：

```bash
cat .agents/runtime_port
```

健康检查：

```bash
PORT_VALUE="$(cat .agents/runtime_port)"
curl --fail --silent "http://127.0.0.1:${PORT_VALUE}/api/health"
```

正常启动应拉起一个 Python Sidecar、一个 `harry-translate-mac` Tauri 宿主
和一个 `harry-tray` 菜单栏入口。

## 8. 音频配置

麦克风模式无需额外驱动。

系统声音模式：

1. 安装 BlackHole 2ch。
2. 在 macOS「音频 MIDI 设置」创建包含 BlackHole 2ch 和实际扬声器的
   多输出设备。
3. 将系统声音输出切到该多输出设备。
4. 在设置的“声音与语言”选择“仅系统声音”或“麦克风 + 系统声音”。

没有回环设备时，后端返回 `loopback_missing` 并回退麦克风。该行为是
设备探测和安全降级，不代表已经实现原生 Core Audio Process Tap。

## 9. 自动验证

### Python

```bash
.venv-v2/bin/python -m pytest -q tests/
.venv-v2/bin/python -m pytest -q tests/core_regression/
.venv-v2/bin/python scripts/smoke_e2e.py --mock
.venv-v2/bin/python scripts/bench_latency.py
.venv-v2/bin/python -m ruff check app scripts tests switch_key.py
.venv-v2/bin/python -m compileall -q app server.py switch_key.py scripts tests
```

### 前端

```bash
cd frontend
npm run typecheck
npm test
npm run build
npm audit
cd ..
```

### Rust

```bash
cargo test --offline --manifest-path src-tauri/Cargo.toml
cargo build --offline --features custom-protocol --manifest-path src-tauri/Cargo.toml
```

### 原生窗口与生命周期

```bash
.venv-v2/bin/python -m pytest -q tests/unit/test_repro_r155s_native_lifecycle.py
for i in 1 2 3 4 5; do
  .venv-v2/bin/python tests/smoke/native_overlay_lifecycle.py
done
```

连续五次都应通过，且测试结束后没有 HARRY 或 fullscreen fixture 进程、
没有新增 `harry-translate-mac-*.ips` 崩溃报告。

### 长会话与发布门禁

```bash
.venv-v2/bin/python -m pytest -q -s tests/bench/test_soak_benchmark.py
./tests/check_markdown_links.sh
```

门槛包括：

- 前端历史卡片最多 1000；
- 2000 句后 100 句与前 100 句耗时比不大于 1.2；
- 250 词冷启动中位数低于 5 ms；
- 默认 corpus 不大于 4096 B，硬上限不大于 6144 B；
- `session.finish` 不超过 250 ms；
- 单窗、单主会话和单一活跃上游不变。

## 10. 构建 arm64 DMG

先安装公开构建依赖：

```bash
uv sync --frozen --python 3.12
uv run pyinstaller --version
```

执行：

```bash
./scripts/package-app.sh
```

产物：

```text
src-tauri/target/aarch64-apple-darwin/release/bundle/dmg/HARRY Translate_0.2.0_aarch64.dmg
```

检查 Sidecar：

```bash
file \
  'src-tauri/target/aarch64-apple-darwin/release/bundle/macos/HARRY Translate.app/Contents/MacOS/harry-sidecar'
```

检查 DMG：

```bash
hdiutil imageinfo \
  'src-tauri/target/aarch64-apple-darwin/release/bundle/dmg/HARRY Translate_0.2.0_aarch64.dmg'
```

当前已验证 SHA-256：

```text
eccb5383c4463f771983ba85a4470a6a176ccc4be19e200da2d12a4e14f5b9fd
```

若重新构建，哈希可能变化，应重新记录。该产物是 ad-hoc 构建，没有 Apple
Developer 签名和 notarization，首次打开可能需要人工确认。

## 11. 依赖与安全检查

```bash
gitleaks git --redact --no-banner --log-opts=--all .
git ls-files | rg '(^|/)\.env($|\.)|\.pem$|\.p12$|\.p8$|secret|credential'
```

第二条命令只应显示安全示例、忽略规则或文档占位内容，不能出现真实凭据。

Python 依赖审计：

```bash
.venv-v2/bin/python -m pip_audit -r requirements.lock
.venv-v2/bin/python -m pip_audit
```

前端依赖审计：

```bash
cd frontend
npm audit
cd ..
```

## 12. 退出与清理

正常退出方式：

1. 在 `harry-tray` 选择“退出悬浮窗”；或
2. 关闭启动器终端；或
3. 正常结束后端进程。

后端停止后，Tauri 宿主会通过健康检查在约 3 秒内退出，不留下桌面浮窗。
不要使用 `killall python` 等宽泛命令。

可安全重新生成的目录：

```text
.venv-v2/
frontend/node_modules/
frontend/dist/
src-tauri/target/
src-tauri/gen/
.pytest_cache/
.ruff_cache/
__pycache__/
```

不要在普通重装中删除：

- `.env`；
- `config/user_settings.json`；
- `config/glossary.json`；
- `legacy/v1/`；
- `.agents/PROJECT_CONTRACT.md` 和 `.agents/decisions/`；
- 用户导出和日志。

## 13. 常见问题

`frontend_build_missing`：

```bash
cd frontend
npm ci
npm run build
cd ..
```

`loopback_missing`：安装 BlackHole 2ch 并创建多输出设备，或继续使用
麦克风模式。

`needs_setup=true`：没有配置 Key，属于正常的无密钥启动状态。

端口不是 8000：正常，应用会在可用端口范围内选择端口，以
`.agents/runtime_port` 为准。

更多排查见 [TROUBLESHOOTING.md](./TROUBLESHOOTING.md)。

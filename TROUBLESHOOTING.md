# HARRY Translate V2 Mac 故障排查

## 1. 先收集最小现场

不要先删除配置、重装环境或执行宽泛杀进程。先检查：

```bash
git status --short --branch
cat .agents/runtime_port 2>/dev/null
pgrep -fl 'harry-translate-mac|fullscreen-fixture'
pgrep -fl 'app.main|legacy/v1/server.py'
```

健康检查：

```bash
PORT_VALUE="$(cat .agents/runtime_port)"
curl --fail --silent "http://127.0.0.1:${PORT_VALUE}/api/health"
```

终端日志只保留最后 30-50 行，并在发送前确认没有密钥、Token、Workspace
实例主机名或本机绝对路径。

## 2. 页面提示 frontend_build_missing

重新构建：

```bash
cd frontend
npm ci
npm run build
cd ..
```

检查：

```bash
test -f frontend/dist/index.html
```

## 3. Key 未配置或鉴权失败

状态：

- `needs_setup=true`：没有可用 Key；
- `auth_error`：Key 无效或无权限；
- `quota_error`：账户额度或限流问题。

不要将真实 Key 写入 `config/user_settings.json`、源码、截图或 Issue。

只检查本机 `.env` 权限：

```bash
stat -f '%Sp %N' .env
```

需要更新 Key 时从设置第 1 栏或本地 `切换API_Key.command` 操作。

## 4. 系统声音不可用

选择系统声音后若出现 `loopback_missing`：

1. 安装 BlackHole 2ch；
2. 在 macOS「音频 MIDI 设置」创建多输出设备；
3. 把系统声音输出切到该设备；
4. 重新选择“仅系统声音”或“麦克风 + 系统声音”。

当前实现是回环设备探测与安全降级，不是原生 Core Audio Process Tap。

## 5. 字幕浮窗无法覆盖全屏应用

检查进程和原生诊断：

```bash
pgrep -fl 'harry-translate-mac|fullscreen-fixture'
PORT_VALUE="$(cat .agents/runtime_port)"
curl --fail --silent "http://127.0.0.1:${PORT_VALUE}/api/health"
```

字幕 overlay 应为：

- `HarrySubtitlePanel`；
- window level `1000`；
- collection behavior 包含 `CanJoinAllSpaces` 与
  `FullScreenAuxiliary`；
- `visible=true`。

专项验证：

```bash
.venv-v2/bin/python -m pytest -q tests/unit/test_repro_r155s_native_lifecycle.py
.venv-v2/bin/python tests/smoke/native_overlay_lifecycle.py
```

Mission Control、登录界面、屏保和独占游戏不在本项承诺内。不要在没有证据
的情况下继续提高 window level。

## 6. 总结图标被裁切

总结图标原生窗口固定为 48x48。预期：

- 图标、角标、边框和内阴影全部位于视口内；
- `scrollWidth === clientWidth`；
- 原生模式没有横向 hover 位移。

测试：

```bash
.venv-v2/bin/python -m pytest -q tests/unit/test_repro_r155s_native_lifecycle.py
```

不要用扩大原生窗口或任意 `overflow:hidden` 掩盖布局越界。

## 7. 总结关闭后无法重开

检查设置中的总结开关。Rust 会把“用户请求启用”和“窗口实际存在”分开
管理，并在 IPC 回调之后执行 reconciliation。

不要通过以下方式修复：

- 把活动 summary WebView 动态转换为瞬态 `NSPanel`；
- 对转换后的窗口调用 `destroy()`；
- 对转换后的窗口调用 `to_window().close()`；
- 永久隐藏窗口来冒充关闭。

这些路径曾在真实 Tao/WebKit 环境中触发 `Rust cannot catch foreign
exceptions`。当前安全路径是普通 `window.close()`。

## 8. 进程结束后浮窗仍在

先只检查本项目进程：

```bash
pgrep -fl 'harry-translate-mac|fullscreen-fixture'
```

检查运行 identity：

```bash
python3 - <<'PY'
import json
from pathlib import Path

path = Path(".agents/runtime_state.json")
if path.exists():
    data = json.loads(path.read_text())
    print({
        key: data.get(key)
        for key in ("instance_token", "overlay_control_url")
    })
PY
```

预期生命周期：

1. Python owner 退出；
2. Tauri 连续 3 次健康检查失败；
3. 宿主发布一次正常退出；
4. overlay、summary 和托盘全部消失。

验证：

```bash
.venv-v2/bin/python tests/smoke/native_overlay_lifecycle.py
```

不要执行 `killall python`。如存在最新崩溃报告，只检查与当前项目相关的
`harry-translate-mac-*.ips`，不要用 `_exit(0)` 或吞异常掩盖问题。

## 9. 端口缺失或被占用

端口在可用范围内动态选择，不能假设一定是 8000。

```bash
ls -l .agents/runtime_port
PORT_VALUE="$(cat .agents/runtime_port)"
curl --fail --silent "http://127.0.0.1:${PORT_VALUE}/api/health"
```

旧的端口文件不是权威状态；健康检查失败时应重新启动。

## 10. 快捷词条或配置保存失败

检查：

```bash
ls -ld config
ls -l config/glossary.json
ls -l config/glossary.json.lock 2>/dev/null
```

保存使用 `flock`、sibling 临时文件、`fsync` 和 `os.replace`。若出现
`glossary_persist_failed`：

1. 检查目录写权限；
2. 检查锁文件是否被异常进程长期占用；
3. 保留错误信息和当前 JSON；
4. 不要直接覆盖成空对象。

## 11. 纠错或总结关闭后仍请求模型

关闭态必须是：

- 0 入队；
- 0 后台任务；
- 0 网络请求；
- 0 总结 DOM 或窗口。

运行：

```bash
.venv-v2/bin/python -m pytest -q \
  tests/core_regression/test_bypass_isolation.py \
  tests/core_regression/test_summary_isolation.py
```

若失败，保留请求日志，但先屏蔽任何认证信息。

## 12. MD/SRT/JSON 不能下载或打开

这是当前正式版的已知待开放项，不是丢失字幕：

- 实时字幕和单会话记录器仍在工作；
- 原子 JSON 保存和格式化基础仍保留；
- 用户侧下载/打开尚未完成；
- 不应删除 `app/transcript_exporter.py`。

后续修复应另立任务，并验证三种格式、零 Token/`usage`/费用和临时文件
零残留。

## 13. 长会话变慢

先运行专项压测：

```bash
.venv-v2/bin/python -m pytest -q -s tests/bench/test_soak_benchmark.py
```

必须保持：

- 1000 张前端卡片硬上限；
- 2000 句尾部/头部耗时比不大于 1.2；
- 清洗器只保留当前段落有界窗口；
- 250 词冷启动中位数低于 5 ms；
- corpus 默认不超过 4096 B，硬上限不超过 6144 B；
- 收尾不超过 250 ms。

不要缩小测试规模或放宽阈值来绕过问题。

## 14. 怀疑密钥已泄露

立即：

1. 在服务商侧撤销或轮换真实 Key；
2. 停止推送；
3. 检查当前跟踪：

```bash
git ls-files
gitleaks git --redact --no-banner --log-opts=--all .
```

4. 检查提交草稿和远程可见性；
5. 清理受影响文件和历史后再发布。

轮换 Key 是第一步；只删除明文不能使旧 Key 重新安全。

## 15. 何时升级处理

出现以下情况时停止继续堆补丁：

- 同一窗口崩溃连续出现两次；
- 5 次原生 smoke 中出现新 `.ips`；
- 零项目进程时仍残留 HARRY 窗口；
- 修复需要破坏单窗、单会话、零计量或零音效不变量；
- 需要关闭 TLS、提交密钥或依赖私有商业授权。

保留失败日志、进程信息、准确命令和最小复现，再进入独立修复任务。

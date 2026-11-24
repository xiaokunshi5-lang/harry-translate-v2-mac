# HARRY Translate V2 Mac R18 验收与发布记录

> **目标平台**：Apple Silicon Mac，macOS 13 及以上
> **验收日期**：2026-11-24
> **用户结论**：R18-01 至 R18-04 全部人工验收通过
> **代码候选**：`47c34804d77e901b2ae79f2bcc936661de840075`
> **候选标签**：`fix-attempt/R18-04/1`
> **验收标签**：`checkpoint-r18-01-ok` 至 `checkpoint-r18-04-ok`、`post-fix-R18`
> **公开发布标签**：`v2.0-r18-ready`

这份记录收口的是 R18 的四项桌面体验修复。它不替代真实会议、真实网络、
真实音频设备或商业签名环境的验收，也不把尚未具备条件的项目写成通过。

## 1. 用户实际验收了什么

用户在同一份 arm64 候选包上完成了以下检查：

| 检查项 | 结果 |
| :--- | :--- |
| 本地 DMG 大小、SHA-256 与 `hdiutil verify` | PASS |
| 安装后确认启动器属于 R18 候选，而不是旧安装 | PASS |
| ad-hoc 签名、未公证与 quarantine 提示说明 | PASS |
| 冷启动只显示原生设置窗口，不弹浏览器、浮窗或麦克风 | PASS |
| 未开启实时翻译时切换全屏 Space，浮窗不误弹 | PASS |
| 关闭设置窗后通过 Dock、Finder、菜单和 `Cmd+,` 恢复 | PASS |
| 辅助浏览器与原生设置窗口隔离 | PASS |
| 顶层只有一个实时翻译滑钮 | PASS |
| 开启后只出现一个字幕窗，状态和真实后台一致 | PASS |
| 关闭后停流、释放麦克风并保留既有字幕历史 | PASS |
| 失败或重新聚焦时使用后端真实状态，不出现假开关 | PASS |
| 设置按钮、应用菜单、`Cmd+Q` 和托盘均可受控退出 | PASS |
| 退出后无 `harry-translate-mac` 或 `harry-sidecar` 残留 | PASS |

## 2. 四轮修复分别解决了什么

### R18-01：启动安静，关闭要真停

应用冷启动时不再主动显示字幕浮窗，也不会因为界面已经存在就开始采集
麦克风或连接上游。用户打开“实时翻译”后，字幕窗和会话才一起启动；关闭时
先暂停会话、关闭字幕连接、结束上游并停止音频。隐藏浮窗不会清空用户已经
看到的字幕历史。

### R18-02：一个滑钮说真话

原来的“启动实时翻译”和“隐藏暂停”两个按钮合并为一个滑钮。滑钮的最终状态
只认后端确认结果；请求进行中会禁用并显示忙碌态，失败时回到真实状态并给出
可读原因。设置页通过被动配置通道、窗口焦点和重新进入页面同步状态，不会
为了刷新开关而建立第二条字幕会话。

### R18-03：原生设置窗口和可控退出

冷启动进入原生 `settings` 窗口，不再打开系统浏览器。关闭窗口只是隐藏，
Dock、Finder、应用菜单、托盘和 `Cmd+,` 都能把它叫回来，也不会顺手弹出
用户没有开启的字幕窗。“退出应用”、菜单退出、`Cmd+Q` 和托盘退出会先停止
翻译和音频，再回收 Sidecar 与 Tauri 宿主。

### R18-04：签名和公证不再含糊

打包脚本现在有两条清楚的轨道：

1. 有 Developer ID 与公证凭据时，先签内层 Sidecar，再签应用、提交 Apple
   公证，分别给 app 和 DMG staple，并用 `spctl` 验证。
2. 没有商业证书时仍然可以构建和本地验证，但报告必须写
   `signature_mode=adhoc_local` 与 `notarized=false`，不能冒充已公证。

当前公开资产走的是第二条轨道。它仍可能在浏览器 quarantine 场景显示
“已损坏”；先验证哈希与 DMG 结构，再只对 `/Applications/HARRY Translate.app`
清除 quarantine。禁止关闭系统全局 Gatekeeper。

## 3. 自动验证证据

| 门禁 | 结果 |
| :--- | :--- |
| Python 全量测试 | `278 passed, 473 subtests passed` |
| 核心回归 | `120 passed, 101 subtests passed` |
| 前端测试 | `83 passed` |
| TypeScript | PASS |
| Rust | `31 passed` |
| 独立看门狗 0 至 5 | PASS，无豁免 |
| 性能记录 | 基线 `0.000984 ms`，当前 `0.000698 ms` |
| 真实 arm64 打包与 `PACKAGE-LIFECYCLE` | PASS |
| `hdiutil verify` | VALID |
| Gitleaks 私人 R18 Git 历史 | 无泄漏 |
| Gitleaks 公开发布 Git 历史 | 无泄漏 |
| 实际签名报告 | `adhoc_local`、`notarized=false`、无 staple、`signing_target=app` |

看门狗 0 至 5 的通过记录来自 `47c3480` 的代码验收状态。验收后新增本契约与
发布文档时，合同指纹按设计发生变化；此时直接用 R18 前的
`pre-fix-r18-20261124` 重跑会自动以 104 提示“契约相对可信基线已改变”。
这不是代码失败，也不能拿旧结果冒充文档改动后的重跑结果。

## 4. 发布产物

- Release：`v2.0-r18-ready`
- 资产：`HARRY.Translate_0.2.0_aarch64.dmg`
- 文件大小：`22,222,266` 字节
- SHA-256：
  `d3f919480afedac359eee8cb26f7848b5bc751cc571da0dd626d71ce96b1efbf`
- 校验文件：`HARRY.Translate_0.2.0_aarch64.dmg.sha256`
- 应用内部版本：`0.2.0`

应用内部版本没有为了这次修复单独升号，所以文件名和 `0.2.0` 本身不能区分
旧包与 R18 包。请以 Release 标签、文件大小和 SHA-256 为准。

旧发布标签 `v2.0-mac-ready` 保留原位置，不移动。新 Release
`v2.0-r18-ready` 才是包含 R18 修复的公开下载入口。

## 5. 尚未验证

- 当前资产没有 Developer ID 公证，干净 Mac 浏览器下载后零提示打开尚未
  通过；这项需要真实商业证书和独立构建。
- 真实 DashScope 长时断连、真实模型质量、真实麦克风听感、BlackHole 与
  双音源环境仍需要操作者本机复验。
- MD/SRT/JSON 用户侧下载和打开仍未开放，不属于 R18 交付。
- 总结窗口仍不承诺加入其他应用的全屏 Space。

## 6. 回滚与排查

回滚 R18 代码前先运行 dry-run：

```bash
./scripts/rollback.sh pre-fix-r18-20261124 --dry-run
```

安装或 Gatekeeper 排查见
[INSTALL.md](../INSTALL.md) 与
[TROUBLESHOOTING.md](../TROUBLESHOOTING.md)。不要通过关闭全局 Gatekeeper
或删除用户数据绕过问题。

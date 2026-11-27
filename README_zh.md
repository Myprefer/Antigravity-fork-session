# Antigravity-fork-session

[English](./README.md) | **简体中文**

Google Antigravity 桌面端的会话管理 Sidecar 插件，用于在桌面端里直接 Fork 会话（支持按历史轮次截断）、在桌面端和 CLI（`agy`）之间互相同步会话，以及跨设备导出/导入会话。

## 为什么做这个

Antigravity CLI（`agy`）支持 `/fork`、`/rewind` 以及从桌面端导入会话，但 Antigravity 桌面端目前还没有原生提供：

1. **会话 Fork / 从历史某一轮分叉**：长对话里想回到前几轮尝试另一种思路时，没法直接分出新分支，只能重开会话重新贴上下文，或者先切到 CLI 导入再操作。
2. **直接打开 CLI 会话**：桌面端和 CLI 使用各自独立的本地目录（`~/.gemini/antigravity` 与 `~/.gemini/antigravity-cli`），在 CLI 里继续聊完或分叉出来的会话，无法直接在桌面端侧边栏打开。

因为两端底层使用的是同一套 SQLite + protobuf 存储结构，所以写了这个零依赖的 Sidecar 插件，直接把按轮次 Fork 和双端同步集成到桌面端右侧面板里。

## 功能

- **当前会话按轮次 Fork**：在右侧面板查看当前会话的每轮提问与 Markdown 回复，可从任意一轮之后或之前分叉出新会话。
- **完整 Fork 与项目切换**：克隆桌面端或 CLI 会话，支持自定义标题、修改归属项目，并直接通知桌面端刷新侧边栏。
- **Desktop ↔ CLI 同步**：把 `~/.gemini/antigravity-cli` 中的 CLI 会话导入桌面端，或将桌面端会话同步到 CLI。
- **跨设备导出与导入**：通过系统原生「另存为」窗口将任意会话导出为 `.agysess.zip`（含完整数据库快照、标题与可选 `brain/` 脑图附件），在另一台 macOS / Linux / Windows 设备的面板中直接导入（也支持直接导入原始 `.db` 文件），自动重写本机项目归属与会话 ID。
- **自动备份与还原**：覆盖同步或覆盖导入前自动备份旧版本，可在「备份」标签页恢复。
- **零依赖**：后端仅使用 Python 3.9+ 标准库，前端为原生 HTML/CSS/JS，跟随 Antigravity 浅色/深色主题。

## 环境要求

- macOS / Linux / Windows
- Python 3.9+
- Google Antigravity Desktop（如需跨端同步，搭配 `agy` CLI 使用）

## 安装

### macOS / Linux

```bash
git clone https://github.com/Myprefer/Antigravity-fork-session.git
cd Antigravity-fork-session
./install.sh
```

开发者可以使用 `./install.sh --symlink` 进行软链接安装。卸载：`./uninstall.sh`。

### Windows (PowerShell)

```powershell
git clone https://github.com/Myprefer/Antigravity-fork-session.git
cd Antigravity-fork-session
.\install.ps1
```

`install.ps1` 会自动检测本机 Python 3.9+ 路径并写入插件的 `sidecar.json`。卸载：`.\uninstall.ps1`。

安装后：

1. 在 Antigravity 桌面端打开 **Customizations**，启用 `Antigravity-fork-session`（`antigravity-fork-session`）及其 Sidecar。
2. 点击右侧面板右上角的 **`+`**，选择 **「会话 Fork 管理」** 打开。

## 独立运行（不安装插件）

```bash
python3 sidecars/session-manager/server.py --port 18765
```

在浏览器访问 `http://127.0.0.1:18765` 即可。

## 可选环境变量

| 变量 | 说明 | 默认值 |
| --- | --- | --- |
| `AGY_DESKTOP_DIR` | 桌面端数据目录 | `~/.gemini/antigravity` |
| `AGY_CLI_DIR` | CLI 数据目录 | `~/.gemini/antigravity-cli` |
| `AGY_MANAGER_PORT` | 独立运行端口 | `18766` |

## 说明

- 服务仅监听 `127.0.0.1`，不对外暴露。
- Fork 与同步使用 SQLite `.backup()` 在暂存目录完成处理后原子替换，不直接修改原会话数据库。
- 本项目依赖 Antigravity 本地 SQLite 与 protobuf 存储结构，如后续官方版本调整本地格式，可能需要同步适配。

## License

[MIT](./LICENSE)

# Windows 本地测试 Klee

这个入口适合先测试聊天、人设和重启后的近期记忆。需要 Python 3.12+
和能连接 Discord/API 的网络，不需要 Docker。音乐播放另需安装 FFmpeg；
NovelAI SDK 是可选依赖。宿主机自动部署程序仍以 Linux 为目标，这次本地
聊天测试不会安装更新服务，也不会自动修改电脑上的代码。

## 1. 下载代码

测试入口目前在 `codex/chat-framework-providers` 分支。如果尚未合并 PR，
下载该分支的 ZIP 并完整解压：

https://github.com/M1zukiii/Kleebot/archive/refs/heads/codex/chat-framework-providers.zip

不要直接在 ZIP 内运行文件。解压后的根目录应有 `start-local.cmd`。

## 2. 准备 Discord 测试机器人

进入 https://discord.com/developers/applications ，选择已有的机器人应用，
或者新建测试应用。在 Bot 页面取得 Token，并启用 Message Content Intent。
通过应用的安装链接或 OAuth2 URL Generator，将机器人加入你自己的测试服务器。
服务器安装需要 `bot`、`applications.commands` scopes；文字测试需查看频道、
发送消息和读取消息历史权限。只在本地启动脚本里输入 Token。

建议使用独立测试应用和测试服务器，避免改动正式机器人的指令。
如果想快速同步斜杠指令，准备测试服务器 ID。Discord 开发者模式启用后，
右键服务器图标可以复制服务器 ID，右键自己的账号可以复制用户 ID。

## 3. 启动

先安装 Python 3.12 或更新版本：
https://www.python.org/downloads/windows/

安装时启用 Python launcher 或 Add Python to PATH。然后双击 `start-local.cmd`。
脚本会依次询问：

1. Discord bot Token（隐藏输入，粘贴时看不到字符是正常的）。
2. 测试服务器 ID（推荐填写）。
3. 你的 Discord 用户 ID（主人指令使用，可留空）。
4. 模型提供商：1 DeepSeek、2 OpenAI、3 不启用 AI。
5. 对应 API Key（隐藏输入；留空时只会返回固定备用台词）。

首次运行会创建 `.venv-local` 并安装依赖，之后直接复用。配置只保存在
本机 `.env.local`；它已从 Git 和 Docker 构建上下文排除。聊天测试数据
保存在 `data/local-test/`。不要把 `.env.local` 上传或发给别人。

看到 `ready: ...` 后，在测试服务器里 `@Klee 你好`。保持启动窗口打开；
按 Ctrl+C 停止。再次双击可以重启。

## 4. 验证近期记忆

在同一个频道里：

1. `@Klee 我最喜欢吃咖喱饭。`
2. 停止并重新启动。
3. `@Klee 我刚才说最喜欢吃什么？`

测试配置默认保存每频道最近 12 条消息，送给模型的上下文上限为 2000 字符。
这验证的是持久化近期上下文，还不是永久人物记忆。`!forgetchat` 可清除当前
频道记忆；服务器内需要管理消息权限。

## 修改配置与可选功能

编辑 `.env.local`，保存后重启。也可以在项目目录的终端运行：

```powershell
.\start-local.cmd --configure
```

`--configure` 会重新填写并替换本地测试配置。若要试 NovelAI 生图，可先安装
完整依赖，再在 `.env.local` 配置 `IMAGE_PROVIDER=novelai` 和 `NOVELAI_TOKEN`：

```powershell
.\start-local.cmd --full
```

OpenAI 生图还需要单独配置 `OPENAI_API_KEY`。单独填写聊天的 `CHAT_API_KEY`
不会启用 OpenAI 生图。

`!开发` 的 GitHub 工作流仍需要先合并到 main 并配置 Actions 密钥；配置
步骤见 DEPLOY.md。本地启动器不会自动启用开发或部署权限。

## 常见问题

- 找不到 Python：安装完成后重新打开终端；运行 `py -3 --version` 检查版本。
- `PrivilegedIntentsRequired`：检查 Bot 页面的 Message Content Intent。
- `LoginFailure`：检查 Discord Token。重新生成 Token 后需修改 `.env.local`。
- 只会说固定备用台词：检查模型 Key、余额、网络和启动窗口里的错误信息。
- 无法播放音乐：聊天测试不安装 FFmpeg；安装并加入 PATH 后再测试音乐。
- 依赖安装失败：保留窗口中的错误文本，分享时删去 Token 或 Key。

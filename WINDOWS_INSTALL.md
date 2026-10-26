# Windows 一键安装

1. 下载 [main 源码 ZIP](https://github.com/M1zukiii/Kleebot/archive/refs/heads/main.zip)，完整解压到准备长期存放的位置。
2. 双击 `install-windows.cmd`。首次需要联网，自动准备 Python 3.12、聊天/语音/音乐/文件/生图依赖、FFmpeg 和桌面「Klee 控制台」。无需 Docker 或 Git。
3. 在控制台点「填写配置 / 密钥」，手动填写 `.env.local` 并保存，再点「启动 Klee」。

必须填写 `DISCORD_TOKEN`。聊天填写 `OPENAI_API_KEY`，或把 `CHAT_PROVIDER` 改成 `deepseek` 并填写 `DEEPSEEK_API_KEY`。其他生图、GitHub 自维护密钥按需填写。

`GUILD_ID` 是服务器 ID，填写可加快斜杠指令同步。`KLEE_OWNER_IDS` 是主人 Discord ID，用于主人专属操作。这两个可以留空，安装器不会猜测。

Discord 开发者门户中的机器人创建、Message Content Intent、邀请进服务器及服务器权限，仍须由账号持有人设置；本地安装不能代替 Discord 授权。自维护生成修复 PR 的 GitHub Actions Secret 和权限也需要在自己的仓库配置。

重新运行安装器会检查依赖、修复控制台快捷方式，**保留已有 `.env.local` 和 `data/`**，不会自动启动机器人、修改服务器或上传密钥。首次没有配置时生成的密钥全部为空。控制台只打开配置文件供你编辑，不会把内容放进安装日志。

关闭控制窗口不会关闭机器人；需要停止时点击窗口内「关闭 Klee」。改完配置后先关闭 Klee，再启动，使新配置生效。

安装日志直接显示在安装窗口；安装失败窗口会保留，修复网络或错误后可重新双击安装器。Python 缺失时只从 python.org 下载固定版本安装程序，并校验 Python Software Foundation 签名，再安装到当前用户目录。已有可用 Python 3.12 会被复用，不更改系统 Python 默认版本。

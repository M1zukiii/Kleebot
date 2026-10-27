# Windows 本机自维护

本机流程：`!开发 需求` → GitHub 生成草稿 PR 并测试 → 主人检查并合并 →
`!更新` → 本机准备、测试、备份、重启 → `!更新状态`。

`/更新` 和 `/更新状态` 也向服务器所有者、拥有 Discord Administrator 权限的管理员开放，原配置主人继续可用。仅角色名称叫“管理员”或只有“管理服务器”权限不算；程序会重新获取成员权限，核验失败时拒绝。`/开发`、`/自维护检查` 仍只对配置主人开放。

## 一次性配置

1. 将本次自维护 PR 合并到 `main`。开发工作流只接受 main 发起的任务；
   更新器也只安装 main 的版本。
2. 在 GitHub 仓库 Settings → Secrets and variables → Actions 新建
   `KLEE_DEVELOPER_API_KEY`，填写开发模型的 DeepSeek API Key。
   可选变量 `KLEE_DEVELOPER_MODEL` 指定模型；默认 `deepseek-flash`。
   此密钥与本机聊天密钥分开；仅配置聊天密钥不会启用开发。
3. 在 Settings → Actions → General 的 Workflow permissions 中启用
   Allow GitHub Actions to create and approve pull requests。
4. 创建仅限本仓库的 GitHub fine-grained token，授权 Actions: write，
   双击 `configure-maintenance.cmd` 在本机填写。向导保留原聊天配置。
5. 停止原来运行 Klee 的窗口，双击 `start-maintained.cmd`。
   不要同时开两个启动器。电脑及此窗口需要保持运行；关闭后不会继续维护。

不要在聊天里发 Token 或 API Key。配置向导将 GitHub token 保存到原 `.env.local`，
GitHub 开发密钥则由主人在 GitHub Secrets 页面填写。

## 第一次验证

在 Discord 发 `!开发 在 help.txt 加一句说明自维护指令的帮助文本`。
检查 GitHub 返回的草稿 PR 和独立验证任务，通过后合并。
再发 `!更新`，准备期间旧 Klee 继续在线，重启时会短暂离线。
重新上线后发 `!更新状态`，应显示更新完成及版本号。

## 更新和恢复

- 更新由主人发送 `!更新` 触发，不会自动合并或安装尚未合并的生成代码。
- `.env.local` 和数据始终留在原位置，新版本保存在 `.klee-local/releases/`。
  每个版本有独立 Python 环境，依赖安装或测试失败不停止正在运行的 Klee。
- 准备阶段不传入 Bot 密钥，也不复制 `.env.local`。Windows 本机测试并非系统沙箱；
  只对已经检查并合并的 main 运行测试。
- 测试通过后停止旧进程，备份数据到 `.klee-local/backups/`（不包含音频缓存），
  启动新版并等待最多 120 秒连接 Discord。确认上线后才记录新版本。
- 上线失败自动重启旧代码。若更新修改了数据结构，可能还需手动从数据备份恢复；
  代码回退不会自动恢复数据库。音乐队列和当前播放会在重启时中断。
- 意外退出会尝试重新启动；10 分钟内连续退出 3 次后停止，避免无限重启。
- 日志：`.klee-local/runtime.log` 为启动和运行日志；`prepare.log` 为依赖与测试日志。
  日志可能包含聊天或外部服务错误信息，分享前请检查并隐藏敏感内容。
- 本机管理器自身是首次安装在原文件夹的程序，后续若修改管理器或启动器，
  需要另行更新这些文件。普通 bot 代码、帮助文字和人设随 main 更新。
- 在未合并自维护代码前发送 `!更新` 会说明 main 缺少框架，并保持原版运行。

普通 `start-local.cmd` 仍可单独测试聊天；Windows 普通模式中的 `!更新` 会提示
改用自维护启动器。

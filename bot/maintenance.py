"""Owner-only maintenance entry points. No shell or Docker access in the bot."""
import json
import os
import re
import time
from pathlib import Path


def is_owner(user_id: int) -> bool:
    owners = {part.strip() for part in os.getenv("KLEE_OWNER_IDS", "").split(",") if part.strip()}
    return str(user_id) in owners


def request_update(data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    target = data_dir / "update_request.json"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps({"requested_at": time.time()}), encoding="utf-8")
    temporary.replace(target)


def update_status(data_dir: Path) -> str:
    try:
        state = json.loads((data_dir / "update_status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "尚无更新状态；需要先在宿主机配置更新服务。"
    return f"更新状态：{state.get('status', 'unknown')}\n版本：{str(state.get('revision', 'unknown'))[:12]}\n{state.get('message', '')}"[:1700]


async def request_development(task: str) -> str:
    import httpx
    task = task.strip()
    if not 1 <= len(task) <= 4000:
        raise ValueError("开发需求请控制在 1–4000 个字符。")
    token = os.getenv("KLEE_GITHUB_ACTIONS_TOKEN", "").strip()
    repo = os.getenv("KLEE_GITHUB_REPO", "M1zukiii/Kleebot").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("KLEE_GITHUB_REPO 格式应为 owner/repo。")
    if not token:
        raise ValueError("尚未配置 KLEE_GITHUB_ACTIONS_TOKEN。")
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            f"https://api.github.com/repos/{repo}/actions/workflows/klee-develop.yml/dispatches",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            json={"ref": "main", "inputs": {"task": task}},
        )
    if response.status_code not in {200, 201, 204}:
        # Never print response bodies or tokens into Discord.
        raise RuntimeError(f"GitHub 未接受开发任务（HTTP {response.status_code}）。请检查权限、Actions 设置和工作流是否已合并。")
    return f"开发任务已提交，Klee 会生成修改并创建草稿 PR。进度：https://github.com/{repo}/actions/workflows/klee-develop.yml"


def maintenance_check(data_dir: Path) -> str:
    supervised = os.getenv('KLEE_LOCAL_SUPERVISED') == '1'
    dispatch = bool(os.getenv('KLEE_GITHUB_ACTIONS_TOKEN','').strip())
    return ('本机更新守护：'+('运行中' if supervised else '未启用')+'\n'+update_status(data_dir)+
            '\n开发任务提交凭据：'+('已配置（尚未验证权限）' if dispatch else '缺少 KLEE_GITHUB_ACTIONS_TOKEN')+
            '\n代码修复模型：在GitHub Actions配置 KLEE_DEVELOPER_API_KEY；本机无法确认远端密钥。'+
            '\n可选变量：KLEE_DEVELOPER_PROVIDER=openai（默认）或deepseek，KLEE_DEVELOPER_MODEL。'+
            '\n流程：生成草稿PR → 独立测试 → 审核合并 → /更新安装，失败时保留旧版。')

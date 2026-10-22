"""Host-side updater. Run periodically; never mount Docker's socket in Kleebot.

Build/test a main-branch revision before stopping the running bot. Deploy using
an image override and roll back the image if Discord readiness fails. Data is
backed up before deployment; restoring data migrations is a manual operation.
"""
import argparse
import fcntl
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path


def run(args, *, cwd=None, check=True):
    return subprocess.run(args, cwd=cwd, check=check, text=True, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, timeout=900)


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value), encoding="utf-8")
    temp.replace(path)


def deploy_with_rollback(compose, image, old_image, write_override, run_command=run):
    write_override(image)
    try:
        run_command(compose + ["up", "-d", "--no-build", "--wait", "--wait-timeout", "120", "kleebot"])
    except Exception:
        if old_image:
            write_override(old_image)
            run_command(compose + ["up", "-d", "--no-build", "--wait", "--wait-timeout", "120", "kleebot"])
        raise


def update(repo, project):
    repo = Path(repo).resolve()
    data = repo / "data"
    data.mkdir(exist_ok=True)
    private = repo / ".klee-updater"
    private.mkdir(mode=0o700, exist_ok=True)
    os.chmod(private, 0o700)
    with open(private / "lock", "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        state_file = private / "state.json"
        state = json.loads(state_file.read_text()) if state_file.exists() else {}
        status_file = data / "update_status.json"
        request_file = data / "update_request.json"
        requested = request_file.exists()
        if requested:
            request_file.unlink()
        revision = "unknown"
        candidate = None
        override = private / "image.json"
        compose = ["docker", "compose", "--project-name", project, "--project-directory", str(repo),
                   "-f", str(repo / "docker-compose.yml"), "-f", str(override)]
        def write_override(image):
            atomic_json(override, {"services": {"kleebot": {"image": image}}})
        try:
            run(["git", "fetch", "origin", "main"], cwd=repo)
            revision = run(["git", "rev-parse", "refs/remotes/origin/main"], cwd=repo).stdout.strip()
            if not re.fullmatch(r"[0-9a-f]{40}", revision):
                raise ValueError("Invalid revision")
            if revision == state.get("revision"):
                atomic_json(status_file, {"status": "current", "revision": revision, "message": "已是已安装的最新版本。"})
                return
            if revision == state.get("failed_revision") and not requested:
                return  # Retry failed revisions only after !update or a new main commit.
            atomic_json(status_file, {"status": "building", "revision": revision, "message": "正在构建并检查新版，现有机器人继续运行。"})
            candidate = Path(tempfile.mkdtemp(prefix="candidate-", dir=private))
            # Use git archive, not a checkout that can execute repository hooks.
            run(["git", "archive", "--format=tar", "--output=" + str(private / "source.tar"), revision], cwd=repo)
            with tarfile.open(private / "source.tar") as source:
                source.extractall(candidate, filter="data")
            image = "kleebot:rev-" + revision
            run(["docker", "build", "--label", "org.opencontainers.image.revision=" + revision,
                 "-t", image, str(candidate)])
            checks = ["docker", "run", "--rm", "--network", "none", "--read-only", "--cap-drop", "ALL",
                      "--security-opt", "no-new-privileges", "--pids-limit", "128", "--memory", "512m",
                      "--cpus", "1", "--tmpfs", "/tmp", "-e", "PYTHONPYCACHEPREFIX=/tmp/pycache", image]
            run(checks + ["python", "-m", "compileall", "-q", "bot", "tests"])
            run(checks + ["python", "-m", "unittest", "discover", "-s", "tests", "-v"])
            old = run(["docker", "inspect", "--format", "{{.Image}}", "kleebot"], check=False)
            old_image = old.stdout.strip() if old.returncode == 0 else None
            # A previously recorded image must remain available for rollback.
            if old_image:
                run(["docker", "image", "inspect", old_image])
            atomic_json(status_file, {"status": "deploying", "revision": revision, "message": "检查通过，正在重启并等待 Discord 就绪。"})
            write_override(image)
            if old_image:
                run(compose + ["stop", "kleebot"])
            try:
                backups = private / "backups"
                backups.mkdir(exist_ok=True)
                with tarfile.open(backups / (str(time.time_ns()) + ".tar.gz"), "w:gz") as backup:
                    backup.add(data, arcname="data", filter=lambda info: None if "/cache" in info.name else info)
            except Exception:
                if old_image:
                    write_override(old_image)
                    run(compose + ["up", "-d", "--no-build", "kleebot"])
                raise
            deploy_with_rollback(compose, image, old_image, write_override, run_command=run)
            state = {"revision": revision, "image": image, "previous_image": old_image}
            atomic_json(state_file, state)
            atomic_json(status_file, {"status": "updated", "revision": revision, "message": "新版已通过检查并连接 Discord。"})
        except Exception as exc:
            state["failed_revision"] = revision
            atomic_json(state_file, state)
            atomic_json(status_file, {"status": "failed", "revision": revision,
                "message": "更新未完成；部署失败时会尝试恢复旧镜像。请检查宿主机日志，修复后用 !update 重试。"})
            print(f"Update failed ({type(exc).__name__}): {exc}", flush=True)
            raise
        finally:
            if candidate:
                shutil.rmtree(candidate, ignore_errors=True)
            (private / "source.tar").unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--project", default="kleebot")
    args = parser.parse_args()
    update(args.repo, args.project)

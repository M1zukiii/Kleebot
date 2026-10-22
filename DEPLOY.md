# Deploy

This bot is easiest to deploy with Docker Compose on a Linux VPS.

## 1. Server Requirements

- Ubuntu 22.04/24.04 or another Linux server
- Docker and Docker Compose
- A Discord bot token

Install Docker on Ubuntu:

```bash
sudo apt update
sudo apt install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

## 2. Upload The Project

Option A, clone from GitHub:

```bash
git clone https://github.com/M1zukiii/Kleebot.git
cd Kleebot
```

Option B, upload the release zip, then:

```bash
unzip Kleebot-release.zip
cd Kleebot
```

## 3. Configure Secrets

```bash
cp .env.example .env
nano .env
```

Set:

```env
DISCORD_TOKEN=your-discord-bot-token
COMMAND_PREFIX=!
GUILD_ID=
YTDLP_COOKIES=/app/data/cookies.txt
```

Do not commit or share `.env`.

Fortune cooldown data is saved in `data/fortune_cooldowns.json`. It resets daily at UTC/GMT 09:00.

## 4. Start

```bash
docker compose up -d --build
docker compose logs -f kleebot
```

Expected log:

```text
ready: Klee#0362 in 1 guild(s)
synced ... command(s)
```

## 5. Update Later

If deployed from GitHub:

```bash
git pull
docker compose up -d --build
```

If deployed from zip, upload a new zip and replace the project files, but keep your existing `.env`.

## Notes

- Your computer does not need to stay online if the bot runs on a VPS.
- Keep Docker running on the VPS.
- For Bilibili, YouTube, or NicoNico sources that need account access, place Netscape cookies at `data/cookies.txt`.
- NicoNico can be played with a full URL or a short ID such as `sm9`. NicoNico tracks are cached under `data/cache` before playback to avoid CDN 403 errors.

## Owner development and automatic deployment

These features require a one-time setup; merging the code alone does not enable
GitHub secrets or install a service on your server. The updater targets a Linux
host with Python 3.12+, Git, Docker, and a modern Compose plugin supporting
`up --wait --wait-timeout`. Do not mount `/var/run/docker.sock` into the bot.

### 1. Enable owner commands

In the server `.env`, set `KLEE_OWNER_IDS` to your Discord user ID (comma-separated
for multiple owners). Empty means disabled. Discord server administrator status
alone does not authorize maintenance commands.

Set `KLEE_GITHUB_ACTIONS_TOKEN` to a fine-grained GitHub token limited to this
repository with **Actions: write**. It dispatches a fixed workflow on `main`;
there is no arbitrary shell command or repository-write API in the Discord bot.
Set `KLEE_GITHUB_REPO=M1zukiii/Kleebot` or your fork's owner/repo.

### 2. Enable development in GitHub

Merge `.github/workflows/klee-develop.yml` into `main`. In repository Settings →
Secrets and variables → Actions, add secret **KLEE_DEVELOPER_API_KEY** with a
DeepSeek API key. Optional repository variable **KLEE_DEVELOPER_MODEL** defaults
to `deepseek-flash`. This is a separate development-model credential from the
bot's chat credential. Enable Actions and allow workflows to create pull requests
under Settings → Actions → General → Workflow permissions. Organization policy
may restrict these settings.

`!develop <requirement>` / `!开发 <需求>` dispatches the workflow and returns a
progress link. The trusted generation job reads code, asks for exact text edits,
validates paths, sizes, and Python syntax, then creates a **draft PR**. It does
not run generated code. Generated edits can touch Python under `bot/` and `tests/`
(except `bot/maintenance.py`), plus the persona, help, and README. They cannot
change secrets, CI, the developer, or host updater. More complex requests may
need manual changes; there is one bounded model request, no unlimited retry loop.

A separate validation job checks the exact candidate commit, with read-only
repository permissions and no developer secret. Follow the workflow link in the
PR to inspect results; generated PRs can also need approval for normal PR CI.
Review the diff and mark the PR ready before merging. Passing tests do not prove
behavioral correctness. PRs are **not automatically merged**. Tasks and generated
code live in your repository's GitHub records; do not include credentials in a task.

### 3. Install the host updater

The examples assume `/opt/Kleebot` is a Git clone of this repository and the
Compose project is named `kleebot` (the default for a directory named Kleebot).
Adjust the paths/project in the service file if your deployment differs. Existing
`.env` and `data/` must stay in this directory. The updater reads those files and
keeps the host checkout untouched; it archives fetched `origin/main` into a
separate candidate directory.

After merging, update the host checkout once to install the trusted updater:

```bash
git pull --ff-only
sudo cp scripts/kleebot-update.service /etc/systemd/system/
sudo cp scripts/kleebot-update.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now kleebot-update.timer
```

The timer checks once a minute. On a new `main` revision the updater builds a
versioned Docker image and runs compilation/tests in a temporary container with
no network, no credentials, no host-data mounts, and bounded resources. Only after
checks pass does it stop the running bot, back up `data/` (excluding audio cache),
and deploy. Discord-ready heartbeats drive the image health check. An unhealthy
replacement triggers a rollback to the previous local image. First installation
has no previous image to restore. Updating disconnects voice playback and clears
the in-memory music queue.

`!update` / `!更新` requests another check, including retrying a failed revision;
`!updatestatus` / `!更新状态` shows the latest host status. These commands require
the owner list. A request does not update code by itself if the timer is absent.
The timer always follows `main`, never an unmerged generated branch. No force
pushes, Git reset, Docker socket access, or self-modification happen in the bot.

Inspect host results:

```bash
systemctl list-timers kleebot-update.timer
journalctl -u kleebot-update.service -n 100 --no-pager
```

State, the deployment image override, and data snapshots live under the private
`.klee-updater/` host directory. Keep the previous Docker image available; do not
prune it while relying on automatic rollback. Backups accumulate; periodically
retain the snapshots you need and remove old ones. Code/image rollback is
automatic when possible; **data migration rollback is manual** using these
snapshots, so review schema-changing updates before merging. If rollback itself
fails, the command fails and host logs require attention.

After enabling the updater, use it for deployments: a plain `docker compose up`
without its private override can select the old unversioned image. To run an
immediate update on the host, use `python3 scripts/update.py --repo /opt/Kleebot`.
Host updater scripts and service definitions are deliberately trusted installed
files; changes to those scripts require updating the host checkout/install again.

End-to-end setup test: request a small change with `!开发`, inspect the draft PR
and validation run, merge it, wait for the timer, and check `!更新状态`. Then test
chat, a music command, and image generation in your own server.

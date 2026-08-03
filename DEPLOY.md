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
git clone https://github.com/M1zukiii/MusicBot.git
cd MusicBot
```

Option B, upload the release zip, then:

```bash
unzip MusicBot-release.zip
cd MusicBot
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
docker compose logs -f musicbot
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

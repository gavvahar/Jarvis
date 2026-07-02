# Deploy Server Setup

Prerequisites for `deploy-main.yml` to work. This runs on the `[self-hosted, homelab]` runner and deploys the stack automatically on every push to `main`.

For runner registration itself, see [self-hosted-runner-setup.md](self-hosted-runner-setup.md).

---

## What the workflow does

1. Checks out the repo into the runner workspace
2. Runs `git pull` to ensure the workspace is current
3. Runs `docker compose pull` to pull fresh base images
4. Runs `docker compose up -d` to start/restart the stack
5. Runs `docker system prune -fa` to clean up unused images
6. Health-checks `http://localhost:5000/login`
7. Dumps compose logs if any step fails

---

## 1 — Docker

Docker and Docker Compose must be installed on the server and accessible to the `jarvis-ci` runner user.

```bash
# Install Docker (if not already installed)
curl -fsSL https://get.docker.com | sh

# Add jarvis-ci to the docker group so it can run docker without sudo
sudo usermod -aG docker jarvis-ci

# Log out and back in (or restart the runner service) for the group to take effect
sudo systemctl restart actions.runner.*
```

Verify:
```bash
sudo -u jarvis-ci docker ps
```

---

## 2 — `.env` file

`compose.yml` reads from `.env` in the working directory. The workflow checks out the repo into a runner workspace directory, so the `.env` needs to be placed there before the deploy runs.

**Recommended approach — store `.env` on the server, copy it in the workflow:**

Place the production `.env` at a fixed path on the server:

```bash
sudo mkdir -p /etc/jarvis
sudo cp .env /etc/jarvis/.env
sudo chmod 600 /etc/jarvis/.env
sudo chown jarvis-ci:jarvis-ci /etc/jarvis/.env
```

Then add a step to `deploy-main.yml` before `docker compose up`:

```yaml
- name: Copy .env
  run: cp /etc/jarvis/.env .env
```

**Alternative — store `.env` content as a GitHub secret:**

In GitHub: **Settings → Secrets → Actions → New repository secret**
Name: `ENV_FILE`, value: paste the entire `.env` contents.

Then add a step:

```yaml
- name: Write .env
  run: echo "${{ secrets.ENV_FILE }}" > .env
```

Fill in all values from [`.env.example`](.env.example) — at minimum:
- `SECRET_KEY` — long random string (`openssl rand -hex 32`)
- `ANTHROPIC_API_KEY` or `OPENAI_API_KEY`
- `DATABASE_URL` and `POSTGRES_PASSWORD`
- `AUTHENTIK_URL`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`
- `APP_URL` — public URL the server is reachable at

---

## 3 — Port 5000

The health check hits `http://localhost:5000/login`. Port 5000 must not be blocked by a local firewall on the server itself:

```bash
# Check if ufw is active
sudo ufw status

# If active, allow local loopback (usually already allowed)
# Port 5000 does NOT need to be open to the internet for the health check —
# localhost is sufficient since the runner runs on the same machine
```

---

## 4 — Verify the setup

Push a commit to `main` and watch **Actions → Deploy to Production**. On first run it will take longer (image build + postgres init). Subsequent runs reuse the layer cache and complete in ~30 seconds.

If the health check fails, the workflow dumps `docker compose logs` automatically — check the Actions run for the output.

---

## Notes

- `docker system prune -fa` runs after `up -d` and removes **all** unused images, not just dangling ones. If you run other Docker workloads on the same server, switch to `docker image prune -f` (dangling only) to avoid removing unrelated images.
- The `jarvis` service in `compose.yml` uses `pull_policy: build`, so `docker compose pull` won't try to pull it from a registry — it builds from the local Dockerfile. `docker compose pull` only fetches the `postgres` image (and `ollama` if the offline profile is active).
- `postgres_data` volume is persistent across deploys — database state survives restarts.

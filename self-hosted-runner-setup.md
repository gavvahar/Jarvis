# Self-Hosted Runner Setup

Instructions for registering the home server and Pi as GitHub Actions runners.

---

## Home Server (`homelab` label)

### Step 1 — Add user to docker group (once)

The runner runs as `nihar` (already exists on the home server). Just make sure it's in the docker group:

```bash
sudo usermod -aG docker nihar
# Log out and back in for the group to take effect
```

### Step 2 — Get the registration token

Go to **github.com/gavvahar/Jarvis → Settings → Actions → Runners → New self-hosted runner**

Select **Linux + x64**. Copy the `--token XXXXX` value — it expires in 1 hour.

### Step 3 — Download and configure the runner

```bash
cd /opt/docker-compose/actions-runner   # actual location on home server

# The GitHub UI shows the exact current download URL — copy it from there
curl -o actions-runner-linux-x64.tar.gz -L <URL_FROM_GITHUB_UI>
tar xzf actions-runner-linux-x64.tar.gz

./config.sh \
  --url https://github.com/gavvahar/Jarvis \
  --token <TOKEN_FROM_UI> \
  --name homelab \
  --labels homelab \
  --unattended
```

### Step 4 — Install as a systemd service

```bash
sudo ./svc.sh install nihar
sudo ./svc.sh start
sudo ./svc.sh status
```

If the service file already exists (re-registration), edit the user directly:

```bash
sudo sed -i 's/User=.*/User=nihar/' /etc/systemd/system/actions.runner.gavvahar-Jarvis.homelab.service
sudo systemctl daemon-reload
sudo systemctl restart actions.runner.gavvahar-Jarvis.homelab.service
```

### Step 5 — Verify

**Settings → Actions → Runners** — `homelab` should appear with a green dot within 30 seconds.

---

## Raspberry Pi (`arm64` label)

Same steps, but select **Linux + ARM64** in the GitHub UI and use `arm64` as the label:

```bash
sudo useradd -m -s /bin/bash nihar

sudo -u nihar bash
cd /home/nihar
mkdir actions-runner && cd actions-runner

curl -o actions-runner-linux-arm64.tar.gz -L <URL_FROM_GITHUB_UI>
tar xzf actions-runner-linux-arm64.tar.gz

./config.sh \
  --url https://github.com/gavvahar/Jarvis \
  --token <TOKEN_FROM_UI> \
  --name pi \
  --labels arm64 \
  --unattended

exit
cd /home/nihar/actions-runner
sudo ./svc.sh install nihar
sudo ./svc.sh start
```

---

## Job routing (after both runners are online)

| Job                | Runner                   |
| ------------------ | ------------------------ |
| `actionlint`       | `ubuntu-latest`          |
| `android-build`    | `ubuntu-latest`          |
| `pip-audit`        | `ubuntu-latest`          |
| `quality` (tox)    | `ubuntu-latest`          |
| `docker-build`     | `[self-hosted, homelab]` |
| `compose-validate` | `[self-hosted, homelab]` |
| `smoke-test`       | `[self-hosted, homelab]` |
| `daemon-test`      | `[self-hosted, arm64]`   |

---

## Notes

- `daemon-test` will queue indefinitely until the Pi runner is registered — it won't fail CI, just wait.
- Token from Step 2 expires after **1 hour** — generate it right before running `config.sh`.
- The `nihar` user needs to be in the `docker` group on the home server so `docker build` works without sudo.
- Keep `android-build`, `actionlint`, `pip-audit`, and `quality` on `ubuntu-latest` — they need clean throwaway environments.

## Playwright prereq (for when `testing-smoke.yml` adds browser checks)

`smoke-test` stays on `[self-hosted, homelab]` even after Playwright is added — the smoke test already needs the real stack and `.env`, and keeping the browser pre-installed avoids the ~150 MB Chromium download that `ubuntu-latest` would incur every run.

Run once on the home server as `nihar`:

```bash
pip3 install playwright
playwright install chromium
playwright install-deps chromium
```

After that, Playwright CI steps work with no per-run download overhead.

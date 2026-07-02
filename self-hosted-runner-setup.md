# Self-Hosted Runner Setup

Instructions for registering the home server and Pi as GitHub Actions runners.

---

## Home Server (`homelab` label)

### Step 1 — Create a dedicated user (once)

```bash
sudo useradd -m -s /bin/bash jarvis-ci
sudo usermod -aG docker jarvis-ci
```

### Step 2 — Get the registration token

Go to **github.com/gavvahar/Jarvis → Settings → Actions → Runners → New self-hosted runner**

Select **Linux + x64**. Copy the `--token XXXXX` value — it expires in 1 hour.

### Step 3 — Download and configure the runner

```bash
sudo -u jarvis-ci bash
cd /home/jarvis-ci
mkdir actions-runner && cd actions-runner

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
exit   # back to your normal user
cd /home/jarvis-ci/actions-runner
sudo ./svc.sh install jarvis-ci
sudo ./svc.sh start
sudo ./svc.sh status
```

### Step 5 — Verify

**Settings → Actions → Runners** — `homelab` should appear with a green dot within 30 seconds.

---

## Raspberry Pi (`arm64` label)

Same steps, but select **Linux + ARM64** in the GitHub UI and use `arm64` as the label:

```bash
sudo useradd -m -s /bin/bash jarvis-ci

sudo -u jarvis-ci bash
cd /home/jarvis-ci
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
cd /home/jarvis-ci/actions-runner
sudo ./svc.sh install jarvis-ci
sudo ./svc.sh start
```

---

## Job routing (after both runners are online)

| Job | Runner |
|---|---|
| `actionlint` | `ubuntu-latest` |
| `android-build` | `ubuntu-latest` |
| `pip-audit` | `ubuntu-latest` |
| `quality` (tox) | `ubuntu-latest` |
| `docker-build` | `[self-hosted, homelab]` |
| `compose-validate` | `[self-hosted, homelab]` |
| `smoke-test` | `[self-hosted, homelab]` |
| `daemon-test` | `[self-hosted, arm64]` |

---

## Notes

- `daemon-test` will queue indefinitely until the Pi runner is registered — it won't fail CI, just wait.
- Token from Step 2 expires after **1 hour** — generate it right before running `config.sh`.
- The `jarvis-ci` user needs to be in the `docker` group on the home server so `docker build` works without sudo.
- Keep `android-build`, `actionlint`, `pip-audit`, and `quality` on `ubuntu-latest` — they need clean throwaway environments.

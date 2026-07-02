# Self-Hosted GitHub Actions Runners — Plan

**Suggested by:** coworker  
**Docs:** https://docs.github.com/en/actions/concepts/runners/self-hosted-runners

## Why

| Problem                                                                                                           | Self-hosted fix                                                    |
| ----------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------ |
| `docker-build` starts cold every run (~5 min)                                                                     | Persistent Docker daemon keeps layer cache → ~30 s after first run |
| ARM-specific daemon packages (`onnxruntime`, `sounddevice`, `rpi_ws281x`) can't be tested on GitHub's x86 runners | Pi runner catches arch breakage before it hits the device          |
| GitHub free-tier minute limits accumulate across many workflows                                                   | Self-hosted minutes are free                                       |

Private repo → no fork-based attack risk (GitHub's main security concern for self-hosted runners).

## Target job routing (final state)

| Job                   | Runner                   | Reason                     |
| --------------------- | ------------------------ | -------------------------- |
| `actionlint`          | `ubuntu-latest`          | Needs clean env, very fast |
| `android-build`       | `ubuntu-latest`          | Needs fresh Android SDK    |
| `pip-audit`           | `ubuntu-latest`          | Security scan, isolated    |
| `quality` (tox)       | `ubuntu-latest`          | Clean Python env preferred |
| `docker-build`        | `[self-hosted, homelab]` | Layer cache win            |
| `compose-validate`    | `[self-hosted, homelab]` | Reuses compose on host     |
| `smoke-test`          | `[self-hosted, homelab]` | Real stack, real network   |
| `daemon-test` _(new)_ | `[self-hosted, arm64]`   | ARM package compatibility  |

## Implementation steps

### 1 — Register the runner on the home server

```bash
# In repo: Settings → Actions → Runners → New self-hosted runner
# Select: Linux, x86_64 (home server) or aarch64 (Pi)

mkdir actions-runner && cd actions-runner
curl -o actions-runner-linux-x64-<VER>.tar.gz -L https://github.com/actions/runner/releases/...
tar xzf actions-runner-linux-x64-<VER>.tar.gz
./config.sh --url https://github.com/gavvahar/Jarvis --token <TOKEN_FROM_UI>
# Token expires after 1 hour — generate it right before running config.sh
```

Add labels during config: `homelab` for the server, `arm64` for a Pi.

Install as a systemd service so it survives reboots:

```bash
sudo ./svc.sh install
sudo ./svc.sh start
sudo ./svc.sh status
```

Run as a dedicated low-privilege user (`jarvis-ci`), not root.

### 2 — Move `docker-build` first

In `.github/workflows/tests.yml`, change:

```yaml
docker-build:
  runs-on: ubuntu-latest # before
```

→

```yaml
docker-build:
  runs-on: [self-hosted, homelab] # after
```

This is the biggest win. Layer cache persists between runs.

### 3 — Move `compose-validate` and `smoke-test`

Same change to `runs-on`. The smoke test (`docker compose up -d --build`) reuses the image layers from Step 2 and hits the real deployment network.

### 4 — Add `daemon-test` job for ARM

Add to `tests.yml`:

```yaml
daemon-test:
  runs-on: [self-hosted, arm64]
  steps:
    - uses: actions/checkout@v4
    - run: pip3 install -r requirements/daemon/requirements.txt
    - run: python3 -c "import onnxruntime, sounddevice; print('daemon deps OK')"
```

### 5 — Security checklist

- [ ] Runner process runs as `jarvis-ci` user, not root
- [ ] No extra `GITHUB_TOKEN` permissions beyond what each workflow already declares
- [ ] Firewall: runner only needs outbound HTTPS (443) to `github.com` and `*.actions.githubusercontent.com`
- [ ] Keep `android-build` / `actionlint` / `pip-audit` on `ubuntu-latest` for clean throwaway environments

## Status

- [ ] Register runner on home server
- [ ] Add `homelab` label, move `docker-build`
- [ ] Move `compose-validate` and `smoke-test`
- [ ] Register Pi with `arm64` label
- [ ] Add `daemon-test` job
- [ ] Security checklist complete

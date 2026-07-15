"""Generate a PR title and description via a remote Ollama instance."""

import json, sys, urllib.error, urllib.request

commits = open("/tmp/commits.txt").read().strip()
diff_stat = open("/tmp/diff_stat.txt").read().strip()

prompt = f"""Generate a GitHub pull request title and description.

Commit messages:
{commits}

Files changed:
{diff_stat}

Rules:
- The title must be plain English, easy to understand, and under 70 characters.
- Do NOT include commit hashes, branch names, or technical prefixes (feat:, fix:, chore:, etc.) in the title.
- Capitalize the first word of the title.

Reply in this exact format (no extra text):
TITLE: <concise plain-English title>
BODY:
## Summary
- <bullet>
- <bullet>

## Test plan
- [ ] <item>
- [ ] <item>"""

payload = json.dumps({"model": "qwen2.5:14b", "prompt": prompt, "stream": False}).encode()

req = urllib.request.Request(
    "https://ollama.gavva.dev/api/generate",
    data=payload,
    headers={"Content-Type": "application/json"},
)

try:
    with urllib.request.urlopen(req, timeout=60) as resp:
        output = json.loads(resp.read()).get("response", "").strip()
except urllib.error.URLError as e:
    print(f"Ollama unreachable: {e}", file=sys.stderr)
    sys.exit(1)

if not output:
    sys.exit(1)

lines = output.splitlines()
title = next(
    (line.removeprefix("TITLE:").strip() for line in lines if line.startswith("TITLE:")),
    "",
)
body_start = next((i for i, line in enumerate(lines) if line.startswith("BODY:")), None)
body = "\n".join(lines[body_start + 1 :]).strip() if body_start is not None else ""

if not title or not body:
    sys.exit(1)

open("/tmp/pr_title.txt", "w").write(title[:70])
open("/tmp/pr_body.txt", "w").write(body)
print("Title:", title)

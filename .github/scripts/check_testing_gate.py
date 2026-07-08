#!/usr/bin/env python3
"""Gate for cascade-merge: only let a push to `testing` cascade once every
required CI workflow has completed successfully for that commit.

Triggered by workflow_run events, which fire once per finished workflow. Two
workflows run in parallel on pushes to testing (Code Quality, Testing Branch
Smoke Test), so a single event only tells us about the workflow that just
finished -- this re-checks all required workflows for the commit so the
cascade waits for the slower one too.
"""

import json, os, urllib.request

REQUIRED_WORKFLOWS = {"Code Quality", "Testing Branch Smoke Test"}


def gh_api_get(path, token):
    req = urllib.request.Request(
        f"https://api.github.com{path}",
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json",
        },
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def main():
    token = os.environ["GH_TOKEN"]
    repo = os.environ["GITHUB_REPOSITORY"]
    sha = os.environ["SHA"]
    output_path = os.environ["GITHUB_OUTPUT"]

    data = gh_api_get(f"/repos/{repo}/actions/runs?head_sha={sha}&per_page=50", token)
    runs = data.get("workflow_runs", [])

    latest = {}
    for run in runs:
        name = run.get("name")
        if name not in REQUIRED_WORKFLOWS:
            continue
        if name not in latest or run["run_number"] > latest[name]["run_number"]:
            latest[name] = run

    ready = True
    for name in sorted(REQUIRED_WORKFLOWS):
        run = latest.get(name)
        if run is None:
            print(f"  ? {name}: no run found yet for {sha[:7]}")
            ready = False
        elif run["status"] != "completed":
            print(f"  … {name}: still running")
            ready = False
        elif run["conclusion"] != "success":
            print(f"  ✗ {name}: concluded '{run['conclusion']}'")
            ready = False
        else:
            print(f"  ✓ {name}: success")

    if not ready:
        print("Not all required checks have succeeded yet for this commit; skipping cascade.")

    with open(output_path, "a") as f:
        f.write(f"ready={'true' if ready else 'false'}\n")


if __name__ == "__main__":
    main()

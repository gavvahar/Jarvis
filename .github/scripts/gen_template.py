"""Fallback PR description generator using commit messages and diff stat."""

import sys

commits = open("/tmp/commits.txt").read().strip().splitlines()
diff_stat = open("/tmp/diff_stat.txt").read().strip()

if not commits:
    print("No commits found", file=sys.stderr)
    sys.exit(1)

bullets = "\n".join(
    "- " + " ".join(line.split()[1:]) for line in commits[:10] if line.strip()
)

tb = "```"
body = f"""## Summary

{bullets}

## Changes

{tb}
{diff_stat}
{tb}

## Test plan

- [ ] Review the changes above
- [ ] Existing tests pass
- [ ] No regressions introduced
"""

open("/tmp/pr_body.txt", "w").write(body)

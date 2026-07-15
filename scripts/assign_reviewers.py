#!/usr/bin/env python3
"""Assign PR reviewers based on recency-weighted git history.

Falls back to CODEOWNERS pattern matching when no history is found.
Reads configuration from environment variables:
  GH_TOKEN, REPO, PR_NUMBER, PR_AUTHOR, REVIEWER_COUNT, MAX_FILES
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from fnmatch import fnmatch

BOTS = frozenset({
    "openshift-merge-robot",
    "openshift-merge-bot[bot]",
    "dependabot[bot]",
    "github-actions[bot]",
    "renovate[bot]",
})

VENDORED_PATTERNS = (
    "vendor/",
    "go.sum",
    "go.mod",
    ".pb.go",
    ".pb.gw.go",
    "_generated.go",
    "zz_generated.",
    "openapi/",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "uv.lock",
    ".terraform.lock.hcl",
)


def is_vendored(path: str) -> bool:
    for pattern in VENDORED_PATTERNS:
        if pattern.endswith("/"):
            if f"/{pattern}" in f"/{path}" or path.startswith(pattern):
                return True
        elif path.endswith(pattern) or path == pattern:
            return True
        elif f"/{pattern}" in f"/{path}":
            return True
    return False


def filter_vendored(files: list[str]) -> tuple[list[str], int]:
    kept = []
    skipped = 0
    for f in files:
        if is_vendored(f):
            skipped += 1
        else:
            kept.append(f)
    return kept, skipped


def recency_weight(commit_date: str, now: datetime) -> int:
    try:
        ts = datetime.fromisoformat(commit_date.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return 0
    age = now - ts
    if age <= timedelta(days=30):
        return 3
    if age <= timedelta(days=90):
        return 2
    if age <= timedelta(days=180):
        return 1
    return 0


def score_commits(
    commits: list[dict],
    pr_author: str,
    now: datetime,
) -> dict[str, int]:
    scores: dict[str, int] = {}
    for commit in commits:
        author = commit.get("author")
        if not author:
            continue
        login = author.get("login", "")
        if not login or login == pr_author or login in BOTS:
            continue
        date = commit.get("commit", {}).get("author", {}).get("date", "")
        weight = recency_weight(date, now)
        if weight > 0:
            scores[login] = scores.get(login, 0) + weight
    return scores


def parse_codeowners(content: str) -> list[tuple[str, list[str]]]:
    rules = []
    for line in content.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        pattern = parts[0]
        owners = [o.lstrip("@") for o in parts[1:] if o]
        if owners:
            rules.append((pattern, owners))
    return rules


def match_codeowners_file(pattern: str, filepath: str) -> bool:
    pat = pattern.lstrip("/")
    if pat.endswith("/"):
        return filepath.startswith(pat) or f"/{filepath}".endswith(f"/{pat.rstrip('/')}/")
    if "/" not in pat:
        return fnmatch(filepath, f"**/{pat}") or fnmatch(os.path.basename(filepath), pat)
    return fnmatch(filepath, pat) or fnmatch(filepath, f"{pat}/**")


def pick_from_codeowners(
    codeowners_content: str,
    changed_files: list[str],
    pr_author: str,
    count: int,
) -> list[str]:
    rules = parse_codeowners(codeowners_content)
    if not rules:
        return []

    owner_hits: dict[str, int] = {}
    for filepath in changed_files:
        matched_owners: list[str] = []
        for pattern, owners in rules:
            if match_codeowners_file(pattern, filepath):
                matched_owners = owners
        for owner in matched_owners:
            if owner != pr_author:
                owner_hits[owner] = owner_hits.get(owner, 0) + 1

    ranked = sorted(owner_hits.items(), key=lambda x: x[1], reverse=True)
    return [login for login, _ in ranked[:count]]


def rank_candidates(scores: dict[str, int], count: int) -> list[str]:
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [login for login, _ in ranked[:count]]


# --- GitHub API helpers ---


def gh_api(endpoint: str, method: str = "GET", input_data: str | None = None, **params: str) -> str:
    cmd = ["gh", "api", endpoint, "--method", method]
    for k, v in params.items():
        cmd.extend(["-f", f"{k}={v}"])
    if input_data:
        cmd.extend(["--input", "-"])
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        input=input_data,
        check=False,
    )
    return result.stdout


def get_changed_files(repo: str, pr_number: str) -> list[str]:
    output = gh_api(f"/repos/{repo}/pulls/{pr_number}/files", paginate=True)
    if not output:
        return []
    files = []
    for item in json.loads(output) if output.startswith("[") else []:
        files.append(item["filename"])
    return files


def get_file_commits(repo: str, filepath: str, since: str) -> list[dict]:
    output = gh_api(f"/repos/{repo}/commits", path=filepath, since=since, per_page="100")
    if not output:
        return []
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        return []


def get_existing_reviewer_count(repo: str, pr_number: str) -> int:
    output = gh_api(f"/repos/{repo}/pulls/{pr_number}/requested_reviewers")
    if not output:
        return 0
    try:
        data = json.loads(output)
        return len(data.get("users", []))
    except json.JSONDecodeError:
        return 0


def get_codeowners(repo: str) -> str | None:
    import base64

    for path in (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS"):
        output = gh_api(f"/repos/{repo}/contents/{path}")
        if not output:
            continue
        try:
            data = json.loads(output)
            return base64.b64decode(data["content"]).decode()
        except (json.JSONDecodeError, KeyError):
            continue
    return None


def assign_reviewers_api(repo: str, pr_number: str, reviewers: list[str]) -> None:
    payload = json.dumps({"reviewers": reviewers})
    result = gh_api(
        f"/repos/{repo}/pulls/{pr_number}/requested_reviewers",
        method="POST",
        input_data=payload,
    )
    if not result:
        print(f"::warning::Failed to assign reviewers: {reviewers}")


def main() -> None:
    repo = os.environ["REPO"]
    pr_number = os.environ["PR_NUMBER"]
    pr_author = os.environ["PR_AUTHOR"]
    reviewer_count = int(os.environ.get("REVIEWER_COUNT", "1"))
    max_files = int(os.environ.get("MAX_FILES", "50"))
    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=180)).strftime("%Y-%m-%dT%H:%M:%SZ")

    existing = get_existing_reviewer_count(repo, pr_number)
    if existing >= reviewer_count:
        print(f"PR already has {existing} reviewer(s) requested. Skipping.")
        return
    needed = reviewer_count - existing

    all_files = get_changed_files(repo, pr_number)
    files, skipped = filter_vendored(all_files)

    if skipped > 0:
        print(f"Skipped {skipped} vendored/generated file(s).")

    def try_codeowners(file_list: list[str]) -> bool:
        print("Falling back to CODEOWNERS.")
        content = get_codeowners(repo)
        if not content:
            print("No CODEOWNERS found. Skipping.")
            return False
        reviewers = pick_from_codeowners(content, file_list, pr_author, needed)
        if reviewers:
            print(f"Assigning from CODEOWNERS: {', '.join(reviewers)}")
            assign_reviewers_api(repo, pr_number, reviewers)
            return True
        print("No matching CODEOWNERS entries. Skipping.")
        return False

    if not files:
        print("No non-vendored files to analyze.")
        try_codeowners(all_files)
        return

    files = files[:max_files]
    print(f"Analyzing {len(files)} changed file(s)...")

    all_scores: dict[str, int] = {}
    for filepath in files:
        commits = get_file_commits(repo, filepath, since)
        file_scores = score_commits(commits, pr_author, now)
        for login, score in file_scores.items():
            all_scores[login] = all_scores.get(login, 0) + score

    if not all_scores:
        print("No eligible reviewers from git history.")
        try_codeowners(files)
        return

    print("\nCandidate scores:")
    for login, score in sorted(all_scores.items(), key=lambda x: x[1], reverse=True):
        print(f"  {login}: {score}")

    reviewers = rank_candidates(all_scores, needed)
    print(f"\nAssigning reviewer(s): {', '.join(reviewers)}")
    assign_reviewers_api(repo, pr_number, reviewers)


if __name__ == "__main__":
    main()

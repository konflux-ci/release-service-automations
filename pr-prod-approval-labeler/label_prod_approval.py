"""Synchronize the PR approval label with current reviews and reviewer permissions."""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
from urllib.parse import quote

LABEL = "prod/needs-approval"
APPROVED_LABEL = "prod/approved"
DECISIVE_STATES = {"APPROVED", "CHANGES_REQUESTED", "DISMISSED"}
WRITE_PERMISSIONS = {"write", "admin"}


def _info(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _warn(message: str) -> None:
    print(f"Warning: {message}", file=sys.stderr, flush=True)


def github_api(
    endpoint: str,
    method: str = "GET",
    payload: dict | None = None,
    paginate: bool = False,
    allow_not_found: bool = False,
) -> dict | list | None:
    """Call the GitHub API and decode its JSON response."""
    command = ["gh", "api", endpoint, "--method", method]
    if paginate:
        command.extend(["--paginate", "--slurp"])
    if payload is not None:
        command.extend(["--input", "-"])
    try:
        response = subprocess.run(
            command,
            input=json.dumps(payload) if payload is not None else None,
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as error:
        # Only an explicit API 404 means CODEOWNERS is absent; other failures must stop us.
        if allow_not_found:
            try:
                error_response = json.loads(error.stdout or "{}")
            except ValueError:
                error_response = {}
            if isinstance(error_response, dict) and str(error_response.get("status")) == "404":
                return None
        raise
    return json.loads(response.stdout) if response.stdout.strip() else None


def compile_codeowners_pattern(pattern: str) -> re.Pattern[str]:
    """Compile directory paths and CODEOWNERS wildcards into a case-sensitive matcher."""
    path = pattern.rstrip("/")
    # A slash anchors the pattern to the root, except a trailing directory slash.
    expression = r"\A" if "/" in path else r"(?:\A|/)"
    segments = path.lstrip("/").split("/")
    for index, segment in enumerate(segments):
        is_last = index == len(segments) - 1
        if segment == "**":
            expression += ".*" if is_last else "(?:[^/]+/)*"
            continue
        for token in re.findall(r"\\.|.", segment):
            if token.startswith("\\") and len(token) == 2:
                expression += re.escape(token[1])
            elif token == "*":
                expression += "[^/]*"
            elif token == "?":
                expression += "[^/]"
            else:
                expression += re.escape(token)
        if not is_last:
            expression += "/"
    if pattern.endswith("/"):
        expression += "/"
    elif pattern.endswith("/*"):
        # CODEOWNERS docs/* covers direct children, unlike directory-wide docs/.
        expression += r"\Z"
    else:
        expression += r"(?:\Z|/)"
    return re.compile(expression)


def has_codeowner(path: str, rules: list[tuple[re.Pattern[str], bool]]) -> bool:
    """Check whether the last matching rule assigns owners to a path."""
    for pattern, has_owners in reversed(rules):
        if pattern.search(path):
            return has_owners
    return False


def load_codeowners(
    repository_endpoint: str, base_sha: str
) -> list[tuple[re.Pattern[str], bool]] | None:
    """Read root CODEOWNERS at the PR's target-branch commit."""
    contents = github_api(
        f"{repository_endpoint}/contents/CODEOWNERS?ref={quote(base_sha, safe='')}",
        allow_not_found=True,
    )
    if contents is None:
        return None
    if contents["type"] != "file" or contents["encoding"] != "base64":
        raise ValueError("Cannot read CODEOWNERS as a base64-encoded file")
    text = base64.b64decode(contents["content"]).decode("utf-8")
    rules = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "!", "\\#")):
            continue
        fields = re.split(r"(?<!\\)\s+", re.split(r"\s+#", line, maxsplit=1)[0])
        pattern = fields[0]
        # GitHub CODEOWNERS does not support negation or character ranges.
        if "[" in pattern or "]" in pattern:
            continue
        has_owners = any(re.fullmatch(r"@\S+|\S+@\S+", owner) for owner in fields[1:])
        rules.append((compile_codeowners_pattern(pattern), has_owners))
    return rules


def requires_prod_approval(
    repository_endpoint: str, pr_number: str, pull_request: dict
) -> bool:
    """Check changed paths against CODEOWNERS without executing PR code."""
    owners = load_codeowners(repository_endpoint, pull_request["base"]["sha"])
    if owners is None:
        # Repositories without root CODEOWNERS retain approval-based labeling for every PR.
        return True
    file_pages = github_api(
        f"{repository_endpoint}/pulls/{pr_number}/files?per_page=100", paginate=True
    )
    changed_files = [changed_file for page in file_pages for changed_file in page]
    # GitHub caps this endpoint at 3,000 files. Never decide from an incomplete listing.
    if len(changed_files) != pull_request["changed_files"]:
        raise ValueError("Changed-file listing is incomplete; leaving labels untouched")
    for changed_file in changed_files:
        paths = [changed_file["filename"]]
        if changed_file.get("previous_filename"):
            paths.append(changed_file["previous_filename"])
        # Renaming a file out of an owned directory still changes production code.
        if any(has_codeowner(path, owners) for path in paths):
            return True
    return False


def current_approvers(reviews: list[dict]) -> list[str]:
    """Return reviewers whose latest decisive review is an approval."""
    latest_reviews = {}
    for review in reviews:
        reviewer = review.get("user")
        if not reviewer or review["state"] not in DECISIVE_STATES:
            continue
        # Submission order matters: an older draft can be submitted after a newer review.
        review_order = (review.get("submitted_at") or "", review["id"])
        previous_review = latest_reviews.get(reviewer["id"])
        if previous_review is None or review_order > previous_review[0]:
            latest_reviews[reviewer["id"]] = (review_order, review)
    return [
        review["user"]["login"]
        for _, review in latest_reviews.values()
        if review["state"] == "APPROVED"
    ]


def synchronize_label(repository: str, pr_number: str) -> None:
    """Update the label to reflect current approvals from repository writers."""
    repository_endpoint = f"repos/{repository}"
    pull_request = github_api(f"{repository_endpoint}/pulls/{pr_number}")
    if pull_request["state"] != "open":
        return
    if not requires_prod_approval(repository_endpoint, pr_number, pull_request):
        _info(f"Skipping PR #{pr_number}: no changed paths are covered by CODEOWNERS")
        return

    review_pages = github_api(
        f"{repository_endpoint}/pulls/{pr_number}/reviews?per_page=100", paginate=True
    )
    reviews = [review for page in review_pages for review in page]
    has_approval = False
    for reviewer in current_approvers(reviews):
        permission = github_api(
            f"{repository_endpoint}/collaborators/{quote(reviewer, safe='')}/permission"
        )
        # GitHub maps the maintain role to write in this API response.
        if permission["permission"] in WRITE_PERMISSIONS:
            has_approval = True
            break

    current_labels = {label["name"] for label in pull_request["labels"]}
    labels_to_remove = [label for label in (LABEL, APPROVED_LABEL) if label in current_labels]
    labels_endpoint = f"{repository_endpoint}/issues/{pr_number}/labels"
    if has_approval and LABEL not in current_labels:
        github_api(labels_endpoint, method="POST", payload={"labels": [LABEL]})
        _info(f"Added {LABEL} to PR #{pr_number}")
    elif not has_approval and labels_to_remove:
        for label in labels_to_remove:
            github_api(f"{labels_endpoint}/{quote(label, safe='')}", method="DELETE")
            _info(f"Removed {label} from PR #{pr_number}")
    else:
        _info(f"Label already matches approvals for PR #{pr_number}")


def main() -> int:
    """Validate required environment variables and synchronize the approval label."""
    for variable_name in ("GH_TOKEN", "GH_REPO", "PR_NUMBER"):
        if not os.environ.get(variable_name):
            _warn(f"{variable_name} must be set")
            return 1
    pr_number = os.environ["PR_NUMBER"]
    if not re.fullmatch(r"[1-9][0-9]*", pr_number):
        _warn("PR_NUMBER must be a positive integer")
        return 1
    try:
        synchronize_label(os.environ["GH_REPO"], pr_number)
    except subprocess.CalledProcessError as error:
        _warn(f"GitHub API request failed (exit {error.returncode})")
        return 1
    except (OSError, ValueError, KeyError, TypeError) as error:
        _warn(f"Unable to synchronize approval label: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

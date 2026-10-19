"""Synchronize the PR approval label with current reviews and reviewer permissions."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from urllib.parse import quote

LABEL = "prod/needs-approval"
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
) -> dict | list | None:
    """Call the GitHub API and decode its JSON response."""
    command = ["gh", "api", endpoint, "--method", method]
    if paginate:
        command.extend(["--paginate", "--slurp"])
    if payload is not None:
        command.extend(["--input", "-"])
    response = subprocess.run(
        command,
        input=json.dumps(payload) if payload is not None else None,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(response.stdout) if response.stdout.strip() else None


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

    has_label = any(label["name"] == LABEL for label in pull_request["labels"])
    labels_endpoint = f"{repository_endpoint}/issues/{pr_number}/labels"
    if has_approval and not has_label:
        github_api(labels_endpoint, method="POST", payload={"labels": [LABEL]})
        _info(f"Added {LABEL} to PR #{pr_number}")
    elif not has_approval and has_label:
        github_api(f"{labels_endpoint}/{quote(LABEL, safe='')}", method="DELETE")
        _info(f"Removed {LABEL} from PR #{pr_number}")
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

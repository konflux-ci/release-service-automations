# PR Prod Approval Labeler GitHub Action

Adds `prod/needs-approval` when an open PR has a current approval from someone
with write, maintain, or admin access. Removes both `prod/needs-approval` and
`prod/approved`, if present, when no qualifying approval remains. When root CODEOWNERS exists, only PRs touching its owned paths
are eligible for label updates. Comment-only reviews do not revoke approvals, and dismissing
one approval leaves both labels in place if another qualifying approval remains.
The action never adds `prod/approved`.

## CODEOWNERS filter

The action reads only `CODEOWNERS` at the PR's target-branch commit.
PR changes to CODEOWNERS cannot change the policy used for that PR.

Matching supports directory paths, root-anchored paths, `*`, `?`, `**`, and escaped
spaces, with case-sensitive matching. `docs/*` matches direct children, while
`docs/` includes descendants.

The last matching rule determines ownership; a rule without owners exempts that
path. Owners select the paths only: approvals still qualify based on write access,
not membership in the listed owners. For example, `/src/ @team` limits labeling
to PRs changing files under the root `src/` directory. Changed paths include removed
files and both names of renamed files.

If root CODEOWNERS is absent, the action adds `prod/needs-approval` when an open
PR has a qualifying approval and removes both `prod/needs-approval` and
`prod/approved`, if present, when no qualifying approval remains.
If root CODEOWNERS exists but no changed paths are owned
(including an empty CODEOWNERS file), it skips review checks and leaves all labels
untouched, including existing `prod/needs-approval` and `prod/approved` labels.
API failures and incomplete changed-file listings fail the action without updating
labels. This includes PRs exceeding GitHub's 3,000-file API limit.

## Inputs

| Name | Description | Required |
|------|-------------|----------|
| `pr-number` | Number of the PR | Yes |
| `github-token` | GitHub token for API calls | Yes |
| `repository` | Repository (`owner/repo`) containing the PR | Yes |

The action uses Python 3.10+ and GitHub CLI, available on GitHub's Ubuntu runners.
No additional Python packages are required.
It uses the supplied token and does not check out or execute PR code.

## Workflows

Copy both files from `workflows/` into the target repository's `.github/workflows/`:

- `pr-review-events.yaml` receives submitted and dismissed reviews.
- `pr-prod-approval-labeler.yaml` updates the approval label after the review workflow completes.

Pin the shared action references to the commit you intend to use. The templates
use `@main`; this action must exist at that ref before they can run.

The two workflows support PRs from forks: review-event runs have read-only tokens,
while the completion-triggered labeler can use `GITHUB_TOKEN` with
`pull-requests: write` and `contents: read` (to read CODEOWNERS). Keep the listener
name and run-title format unchanged unless the labeler's workflow filter and title parser are updated too.

Create `prod/needs-approval` in the repository's label list. To invalidate approvals
on reviewable pushes, enable GitHub's stale-approval dismissal setting. The action
uses GitHub's review state; it does not remove the label merely because a commit
SHA changed during a rebase.

## Verification

Run the mocked API checks:

```sh
python3 -m unittest discover -s pr-prod-approval-labeler -p 'test_*.py'
```

For an end-to-end test, put both workflows on the default branch, open a test PR touching a CODEOWNERS path,
and have another user with write access approve it. Check the Actions tab and
verify that the label appears. Dismiss the approval and verify removal. Also test
a PR from a fork. The completion-triggered workflow cannot run until its file
exists on the default branch. No additional token secret is required.

Also verify that a PR outside CODEOWNERS paths leaves labels untouched.

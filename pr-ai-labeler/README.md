# PR AI Labeler GitHub Action

Automatically adds labels to PRs when commit messages contain Assisted-By or Generated-By trailers.

## Inputs

| Name           | Description                              | Required |
|----------------|------------------------------------------|----------|
| `pr-number`    | Number of the PR                         | ✅       |
| `github-token` | GitHub token for API calls               | ✅       |
| `repository`   | Repository (owner/repo) containing the PR | ✅       |

## Environment Variables

- `GH_TOKEN`: GitHub token (e.g. `${{ secrets.GITHUB_TOKEN }}`)
- `GITHUB_REPOSITORY`: Repository (e.g. `${{ github.repository }}`)
- `PR_NUMBER`: Number of the PR (e.g. `${{ github.event.pull_request.number }}`)

## Labels

The repository must have these labels (create them under Settings → Labels if needed):

- `ai-assisted`: Added when any commit message contains **Assisted-By:** (case-insensitive)
- `ai-generated`: Added when any commit message contains **Generated-By:** (case-insensitive)

## How It Works

* On PR open or new pushes (`opened`, `synchronize`), the workflow fetches all commits in the PR.
* Commit messages are scanned for the **Assisted-By** or **Generated-By** trailers (case-insensitive).
* The corresponding labels (`ai-assisted`, `ai-generated`) are added to the PR if not already present.

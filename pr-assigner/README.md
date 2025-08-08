# PR Assigner GitHub Action

Automatically assigns reviewers to PRs and notifies them via Slack.

## Inputs

| Name              | Description                                         | Required |
|-------------------|-----------------------------------------------------|----------|
| `event-type`      | GitHub event action (`opened`, etc.)               | ✅       |
| `pr-number`       | Number of the PR                                    | ✅       |
| `removed-reviewer`| Login of removed reviewer (for `review_request_removed`) | ❌     |

## Environment Variables

- `GH_TOKEN`: GitHub token (e.g. `${{ secrets.GITHUB_TOKEN }}`)
- `SLACK_WEBHOOK`: Slack webhook for notifications

## User Map

The file `user_map.yaml` in the repo root should define user mappings like:

```yaml
users:
  username:
    slack_id: U123456
    notify: true
    assign: true

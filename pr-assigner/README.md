# PR Assigner GitHub Action

Automatically assigns PRs to team members and notifies them via Slack.

## Inputs

| Name              | Description                                         | Required |
|-------------------|-----------------------------------------------------|----------|
| `event-type`      | GitHub event action (`opened`, `unassigned`, etc.)   | ✅       |
| `pr-number`       | Number of the PR                                     | ✅       |
| `removed-assignee`| Login of removed assignee (for `unassigned` events)  | ❌       |

## Environment Variables

- `GH_TOKEN`: GitHub token (e.g. `${{ secrets.GITHUB_TOKEN }}`)
- `SLACK_WEBHOOK`: Slack webhook for notifications

## User Map

The file `user_map.yaml` in the repo root should define user mappings like:

```yaml
users:
  username:
    slack_id: U123456
    notify: true   # Whether to send Slack notifications
    assign: true   # Whether this user can be assigned to PRs
```

## How It Works
* On PR creation (opened), the action randomly selects eligible assignees from the user_map.yaml file.
* On assignee removal (unassigned), a new eligible assignee is selected and assigned.
* Slack notifications are sent according to the notify setting in the user map.

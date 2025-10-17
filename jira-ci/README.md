# JIRA CI - Ticket Promotion Action

A reusable GitHub Action that automatically promotes JIRA tickets from one stage to another in the release process.

## Usage

```yaml
- name: Run JIRA CI Action
  uses: konflux-ci/release-service-automations/jira-ci@main
  with:
    jira_url: "https://issues.redhat.com"
    promotion_type: "development-to-staging"
    metadata_file: "parsed-tickets.json"
    jira_token: ${{ secrets.JIRA_TOKEN }}
    dry_run: "false"
```

## Inputs

| Input            | Description                                  | Required | Default                     |
|------------------|----------------------------------------------|----------|-----------------------------|
| `jira_url`       | The URL of the JIRA instance                 | No       | `https://issues.redhat.com` |
| `promotion_type` | Type of promotion to perform                 | Yes      | -                           |
| `metadata_file`  | Path to JSON file containing ticket metadata | Yes      | -                           |
| `dry_run`        | Run in dry run mode "true" or "false"        | No       | `false`                     |
| `jira_token`     | JIRA API token for authentication            | Yes      | -                           |

### Promotion Types

- `development-to-staging`: Promotes tickets from development to staging
- `staging-to-production`: Promotes tickets from staging to production

## Metadata File Format

The metadata file should be a JSON array containing ticket information:

```json
[
  {
    "ticket": "RELEASE-123",
    "pr_url": "https://github.com/your-org/your-repo/pull/1"
  },
  {
    "ticket": "RELEASE-321"
  },
  {
    "ticket": "OTHER-456",
    "pr_url": "https://github.com/your-org/your-repo/pull/2"
  }
]
```

## Required Secrets

- `JIRA_TOKEN`: JIRA API token

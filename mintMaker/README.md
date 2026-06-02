# Renovate Presets

Shared [Renovate config presets](https://docs.renovatebot.com/config-presets/) for Konflux Release Service repositories,
used by [MintMaker](https://github.com/konflux-ci/mintmaker).

## Presets

| Preset  | File           | What it adds                                                         |
|---------|----------------|----------------------------------------------------------------------|
| default | `default.json` | `rebaseWhen`, tekton schedule and GitHub Actions with `pinDigests`   |
| utils   | `utils.json`   | Extends default, weekly lock file maintenance and pep621 pinned deps |
| catalog | `catalog.json` | Extends default, custom tekton paths and image filtering             |

## Usage

Base only:

```
{
  "$schema": "https://docs.renovatebot.com/renovate-schema.json",
  "extends": ["github>konflux-ci/release-service-automations//mintMaker/default"]
}
```

Base and catalog specific tekton paths and image filtering:

```
{
  "$schema": "https://docs.renovatebot.com/renovate-schema.json",
  "extends": ["github>konflux-ci/release-service-automations//mintMaker/catalog"]
}
```

## Adding a preset

1. Create `<name>.json` in the directory.
2. Extend default if the new preset should include the base.
3. Reference from repos as `github>konflux-ci/release-service-automations//mintMaker/<name>`

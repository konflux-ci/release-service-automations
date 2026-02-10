const { Octokit } = require("@octokit/core");
const fetch = require("node-fetch");

const octokit = new Octokit({
  auth: process.env.GH_TOKEN,
  request: { fetch },
});

const [owner, repo] = process.env.GITHUB_REPOSITORY.split("/");
const prNumber = process.env.PR_NUMBER;

const ASSISTED_BY_RE = /assisted-by\s*:/i;
const GENERATED_BY_RE = /generated-by\s*:/i;

async function getPRCommits() {
  const { data } = await octokit.request(
    "GET /repos/{owner}/{repo}/pulls/{pull_number}/commits",
    { owner, repo, pull_number: prNumber }
  );
  return data;
}

async function getIssueLabels() {
  const { data } = await octokit.request(
    "GET /repos/{owner}/{repo}/issues/{issue_number}",
    { owner, repo, issue_number: prNumber }
  );
  return (data.labels || []).map((l) => l.name);
}

const AI_LABELS = ["ai-assisted", "ai-generated"];

async function addLabels(labels) {
  if (labels.length === 0) return;
  await octokit.request(
    "POST /repos/{owner}/{repo}/issues/{issue_number}/labels",
    { owner, repo, issue_number: prNumber, data: { labels } }
  );
}

async function removeLabel(label) {
  await octokit.request(
    "DELETE /repos/{owner}/{repo}/issues/{issue_number}/labels/{name}",
    { owner, repo, issue_number: prNumber, name: label }
  );
}

(async () => {
  let commits;
  try {
    commits = await getPRCommits();
  } catch (err) {
    console.error("Failed to get PR commits:", err);
    process.exit(1);
  }

  let needAiAssisted = false;
  let needAiGenerated = false;

  for (const c of commits) {
    const msg = (c.commit && c.commit.message) || "";
    if (ASSISTED_BY_RE.test(msg)) needAiAssisted = true;
    if (GENERATED_BY_RE.test(msg)) needAiGenerated = true;
  }

  const labelsToAdd = [];
  if (needAiAssisted) labelsToAdd.push("ai-assisted");
  if (needAiGenerated) labelsToAdd.push("ai-generated");

  let currentLabels;
  try {
    currentLabels = await getIssueLabels();
  } catch (err) {
    console.error("Failed to get issue labels:", err);
    process.exit(1);
  }

  if (labelsToAdd.length === 0) {
    const toRemove = AI_LABELS.filter((l) => currentLabels.includes(l));
    if (toRemove.length === 0) {
      console.log("No Assisted-By or Generated-By found in PR commits. No AI labels to remove.");
      return;
    }
    try {
      for (const label of toRemove) {
        await removeLabel(label);
      }
      console.log("Removed labels (no trailers in commits):", toRemove.join(", "));
    } catch (err) {
      console.error("Failed to remove labels:", err);
      process.exit(1);
    }
    return;
  }

  const missing = labelsToAdd.filter((l) => !currentLabels.includes(l));
  if (missing.length === 0) {
    const toRemove = AI_LABELS.filter(
      (l) => currentLabels.includes(l) && !labelsToAdd.includes(l)
    );
    if (toRemove.length === 0) {
      console.log("Labels already correct:", labelsToAdd.join(", "));
      return;
    }
    try {
      for (const label of toRemove) {
        await removeLabel(label);
      }
      console.log("Removed labels:", toRemove.join(", "));
    } catch (err) {
      console.error("Failed to remove labels:", err);
      process.exit(1);
    }
    return;
  }

  try {
    await addLabels(missing);
    console.log("Added labels:", missing.join(", "));
  } catch (err) {
    console.error("Failed to add labels:", err);
    process.exit(1);
  }
})();

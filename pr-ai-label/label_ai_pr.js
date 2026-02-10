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

async function addLabels(labels) {
  if (labels.length === 0) return;
  await octokit.request(
    "POST /repos/{owner}/{repo}/issues/{issue_number}/labels",
    { owner, repo, issue_number: prNumber, data: { labels } }
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

  if (labelsToAdd.length === 0) {
    console.log("No Assisted-By or Generated-By found in PR commits. Skipping labels.");
    return;
  }

  let currentLabels;
  try {
    currentLabels = await getIssueLabels();
  } catch (err) {
    console.error("Failed to get issue labels:", err);
    process.exit(1);
  }

  const missing = labelsToAdd.filter((l) => !currentLabels.includes(l));
  if (missing.length === 0) {
    console.log("Labels already present:", labelsToAdd.join(", "));
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

const { Octokit } = require("@octokit/core");
const { retry } = require("@octokit/plugin-retry");
const fetch = require("node-fetch");

const OctokitWithRetry = Octokit.plugin(retry);
const octokit = new OctokitWithRetry({
  auth: process.env.GH_TOKEN,
  request: { fetch },
});

const [owner, repo] = process.env.GITHUB_REPOSITORY.split("/");
const prNumber = process.env.PR_NUMBER;

const ASSISTED_BY_RE = /assisted-by\s*:/i;
const GENERATED_BY_RE = /generated-by\s*:/i;
const MADE_BY_RE = /made-by\s*:/i;

const MAX_RETRIES = 3;
const RETRY_DELAY_MS = 2000;

function isRetryableError(err) {
  if (!err) return false;
  const msg = String(err.message || "");
  if (msg.includes("ECONNREFUSED") || msg.includes("ETIMEDOUT") || msg.includes("ECONNRESET")) return true;
  const status = err.status != null ? err.status : (err.response && err.response.status) != null ? err.response.status : err.code;
  if (typeof status === "number" && status >= 500) return true;
  return false;
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function withRetry(fn, label) {
  let lastErr;
  for (let attempt = 1; attempt <= MAX_RETRIES; attempt++) {
    try {
      return await fn();
    } catch (err) {
      lastErr = err;
      if (attempt < MAX_RETRIES && isRetryableError(err)) {
        const delay = RETRY_DELAY_MS * Math.pow(2, attempt - 1);
        console.warn(`${label} failed (attempt ${attempt}/${MAX_RETRIES}), retrying in ${delay}ms:`, err.message || err);
        await sleep(delay);
      } else {
        throw err;
      }
    }
  }
  throw lastErr;
}

async function getPRCommits() {
  return withRetry(async () => {
    const { data } = await octokit.request(
      "GET /repos/{owner}/{repo}/pulls/{pull_number}/commits",
      { owner, repo, pull_number: prNumber }
    );
    return data;
  }, "getPRCommits");
}

async function getIssueLabels() {
  return withRetry(async () => {
    const { data } = await octokit.request(
      "GET /repos/{owner}/{repo}/issues/{issue_number}",
      { owner, repo, issue_number: prNumber }
    );
    return (data.labels || []).map((l) => l.name);
  }, "getIssueLabels");
}

const AI_LABELS = ["ai-assisted", "ai-generated"];

async function addLabels(labels) {
  if (labels.length === 0) return;
  return withRetry(async () => {
    await octokit.request(
      "POST /repos/{owner}/{repo}/issues/{issue_number}/labels",
      { owner, repo, issue_number: prNumber, data: { labels } }
    );
  }, "addLabels");
}

async function removeLabel(label) {
  return withRetry(async () => {
    await octokit.request(
      "DELETE /repos/{owner}/{repo}/issues/{issue_number}/labels/{name}",
      { owner, repo, issue_number: prNumber, name: label }
    );
  }, "removeLabel");
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
    if (MADE_BY_RE.test(msg)) needMadeBy = true;
  }

  const labelsToAdd = [];
  if (needAiAssisted) labelsToAdd.push("ai-assisted");
  if (needAiGenerated) labelsToAdd.push("ai-generated");
  if (needMadeBy) labelsToAdd.push("made-by-ai");

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
      console.log("No Assisted-By or Generated-By or Made-By found in PR commits. No AI labels to remove.");
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

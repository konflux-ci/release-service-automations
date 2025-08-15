const { Octokit } = require("@octokit/core");
const fetch = require("node-fetch");
const yaml = require("yaml");
const fs = require("fs").promises;

const octokit = new Octokit({
    auth: process.env.GH_TOKEN,
    request: { fetch }
});

const [owner, repo] = process.env.GITHUB_REPOSITORY.split("/");
const prNumber = process.env.PR_NUMBER;
const eventType = process.env.EVENT_TYPE; // now expects "opened", "ready_for_review", "unassigned"
const userMapFilePath = process.env.USER_MAP_FILE_PATH;
const removedAssignee = process.env.REMOVED_ASSIGNEE;

async function getUserMap() {
    try {
        const text = await fs.readFile(userMapFilePath, "utf8");
        const parsed = yaml.parse(text);
        return parsed.users || {};
    } catch (error) {
        console.error("Error loading user map from local file:", error);
        return {};
    }
}

async function getPRDetails() {
    const res = await octokit.request("GET /repos/{owner}/{repo}/pulls/{pull_number}", {
        owner, repo, pull_number: prNumber
    });
    return res.data;
}

async function assignPRsToUsers(newAssignees, currentPR) {
    const currentAssignees = currentPR.assignees.map(a => a.login);
    const assigneesToAdd = newAssignees.filter(a => !currentAssignees.includes(a));

    if (assigneesToAdd.length > 0) {
        try {
            await octokit.request("POST /repos/{owner}/{repo}/issues/{issue_number}/assignees", {
                owner, repo, issue_number: prNumber,
                assignees: assigneesToAdd
            });
        } catch (error) {
            console.error("❌ Failed to assign PR to users:", error);
            process.exit(1);
        }
        return assigneesToAdd;
    }
    return [];
}

function mention(users, userMap) {
    return users.map(u => {
        const info = userMap[u];
        if (!info) return `@${u}`;
        return info.notify === false ? `@${u}` : `<@${info.slack_id}>`;
    }).join(" ");
}

async function notifySlack(text) {
    if (!process.env.SLACK_WEBHOOK) {
        console.warn("⚠️ SLACK_WEBHOOK env var not set. Skipping Slack notification.");
        return;
    }
    try {
        await fetch(process.env.SLACK_WEBHOOK, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ text }),
        });
    } catch (error) {
        console.error("❌ Failed to send Slack notification:", error);
        process.exit(1);
    }
}

(async () => {
    let pr;
    try {
        pr = await getPRDetails();
    } catch (error) {
        console.error("❌ Failed to get PR details:", error);
        process.exit(1);
    }

    const prTitle = pr.title;
    const prUrl = pr.html_url;
    const prLink = `<${prUrl}|${prTitle}>`;
    const userMap = await getUserMap();

    if (Object.keys(userMap).length === 0) {
        console.warn("⚠️ User map is empty. No assignees will be added.");
    }

    const allUsers = Object.entries(userMap)
        .filter(([_, v]) => v.assign !== false)
        .map(([k]) => k);
    console.log("Eligible assignees (assign != false):", allUsers);

    const author = pr.user.login;
    let candidates = allUsers.filter(u => u !== author);
    const currentAssignees = pr.assignees.map(a => a.login);

    if (eventType === "unassigned" && removedAssignee) {
        console.log(`Excluding removed assignee from candidate pool for this run: ${removedAssignee}`);
        candidates = candidates.filter(u => u !== removedAssignee);
    }

    console.log("Available assignees after filtering author/current assignees:", candidates);

    const shouldAssign =
        (eventType === "opened" && !pr.draft) ||
        eventType === "ready_for_review" ||
        eventType === "unassigned";

    if (shouldAssign) {
        const needed = 2 - currentAssignees.length;

        if (needed > 0) {
            const available = candidates.filter(u => !currentAssignees.includes(u));
            console.log("Available users:", available);

            const toAdd = [];
            const pool = available.slice();
            while (toAdd.length < needed && pool.length > 0) {
                const idx = Math.floor(Math.random() * pool.length);
                toAdd.push(pool.splice(idx, 1)[0]);
            }

            let msg = null;
            if (toAdd.length > 0) {
                const addedAssignees = await assignPRsToUsers(toAdd, pr);
                if (addedAssignees.length > 0) {
                    if (eventType === "unassigned" && toAdd.length === 1) {
                        msg = `⚠️ Assignee removed from PR #${prNumber} ${prLink} in \`${repo}\`. Added ${mention(addedAssignees, userMap)} to meet assignment requirements.`;
                    } else if (eventType === "unassigned") {
                        msg = `⚠️ Assignees removed from PR #${prNumber} ${prLink} in \`${repo}\`. Added ${mention(addedAssignees, userMap)} to meet assignment requirements.`;
                    } else {
                        msg = `👥 Assignee update for PR #${prNumber} ${prLink} in \`${repo}\`: ${mention(addedAssignees, userMap)}.`;
                    }
                }
            } else if (needed > 0 && available.length === 0) {
                msg = `❗️ Assignees needed for PR #${prNumber} ${prLink} in \`${repo}\` but no available candidates to assign.`;
            }

            if (msg) {
                console.log(msg);
                await notifySlack(msg);
            }
        } else if (eventType === "unassigned" && currentAssignees.length >= 2) {
            console.log(`Assignee(s) removed from PR #${prNumber}, but enough assignees (${currentAssignees.length}) still remain. No new assignment needed.`);
        }
    }
})();

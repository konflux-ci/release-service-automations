const fetch = require("node-fetch");

function parseDate(dateStr) {
    const y = parseInt(dateStr.substring(0, 4), 10);
    const m = parseInt(dateStr.substring(4, 6), 10) - 1;
    const d = parseInt(dateStr.substring(6, 8), 10);
    return new Date(Date.UTC(y, m, d));
}

function parseEvents(icsText) {
    const events = [];
    const blocks = icsText.split("BEGIN:VEVENT");

    for (let i = 1; i < blocks.length; i++) {
        const block = blocks[i].split("END:VEVENT")[0];
        const lines = block.replace(/\r\n /g, "").split(/\r?\n/);

        let startDate = null;
        let endDate = null;
        let summary = "";
        let isFullDay = false;

        for (const line of lines) {
            if (line.startsWith("DTSTART;VALUE=DATE:")) {
                isFullDay = true;
                startDate = parseDate(line.substring("DTSTART;VALUE=DATE:".length).trim());
            } else if (line.startsWith("DTEND;VALUE=DATE:")) {
                endDate = parseDate(line.substring("DTEND;VALUE=DATE:".length).trim());
            } else if (line.startsWith("SUMMARY:")) {
                summary = line.substring("SUMMARY:".length).trim();
            }
        }

        if (isFullDay && startDate && summary) {
            if (!endDate) {
                endDate = new Date(startDate);
                endDate.setUTCDate(endDate.getUTCDate() + 1);
            }
            events.push({ startDate, endDate, summary });
        }
    }

    return events;
}

function buildSlackIdToGithubMap(userMap) {
    const map = new Map();
    for (const [githubUser, info] of Object.entries(userMap)) {
        if (info.slack_id) {
            map.set(info.slack_id.toLowerCase(), githubUser);
        }
    }
    return map;
}

const PTO_PATTERN = /^(.+?)\s*-\s*(?:pto|holiday|ooo)$/i;

async function getUsersOnPTO(calendarUrl, userMap) {
    const ptoUsers = new Set();

    if (!calendarUrl) {
        return ptoUsers;
    }

    try {
        const res = await fetch(calendarUrl);
        if (!res.ok) {
            console.warn(`⚠️ Failed to fetch PTO calendar (HTTP ${res.status}). Proceeding without PTO filtering.`);
            return ptoUsers;
        }

        const icsText = await res.text();
        const events = parseEvents(icsText);
        const slackToGithub = buildSlackIdToGithubMap(userMap);

        const now = new Date();
        const todayStart = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()));
        const todayEnd = new Date(todayStart);
        todayEnd.setUTCDate(todayEnd.getUTCDate() + 1);

        for (const event of events) {
            if (event.startDate < todayEnd && event.endDate > todayStart) {
                const match = event.summary.match(PTO_PATTERN);
                if (match) {
                    const username = match[1].trim().toLowerCase();
                    const githubUser = slackToGithub.get(username);
                    if (githubUser) {
                        ptoUsers.add(githubUser);
                    }
                }
            }
        }
    } catch (error) {
        console.warn("⚠️ PTO calendar check failed. Proceeding without PTO filtering.");
    }

    return ptoUsers;
}

module.exports = { getUsersOnPTO };

import argparse
import logging
import os
import requests
import json
import re
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

RELEASE_TICKET_PATTERN = r"RELEASE-\d+"
NON_RELEASE_TICKET_PATTERN = r"[A-Z]+-\d+"
CLOSED_STATUS = "closed"
RELEASE_PENDING_STATUS = "release pending"
JIRA_HTTP_TIMEOUT = 30


class JiraError(RuntimeError):
    """Base error for Jira related errors."""


class JiraHTTPError(JiraError):
    """HTTP call to Jira failed."""


class JiraJSONError(JiraError):
    """Invalid or unexpected JSON from Jira."""


class JiraClient:
    def __init__(self, logger, args):
        self.logger = logger
        self.args = args
        self.token = self._load_env()
        self.session = self._load_session()

    def _load_env(self):
        """
        Load the JIRA token from the environment variable 'JIRA_TOKEN'.
        Raises an error if the token is not set.
        """
        token = os.getenv("JIRA_TOKEN")
        if not token:
            raise JiraError("'JIRA_TOKEN' is not set as env variable")
        return token

    def _load_session(self):
        """
        Create and return a session object configured
        for Jira API calls.
        """
        session = requests.Session()
        retry_strategy = Retry(
            total=5,
            read=5,
            connect=5,
            backoff_factor=5.0,
            status_forcelist=(429, 500, 503, 504),
            raise_on_status=False,
            allowed_methods=frozenset({"GET", "PUT", "POST"}),
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        session.headers.update(
            {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.token}",
            }
        )
        return session

    def _call_jira_api(self, endpoint, method, data=None):
        """
        Make a request to the JIRA API and return the response.
        Raises an error if the request fails with a HTTP error.
        """
        url = f"{self.args.jira_url}/rest/api/2/{endpoint}"
        try:
            response = self.session.request(
                method, url, json=data, timeout=JIRA_HTTP_TIMEOUT
            )
            response.raise_for_status()
            return response
        except requests.exceptions.HTTPError as e:
            raise JiraHTTPError(
                f"HTTP error calling JIRA API {e.response.status_code} "
                f"url: {url} message: {e.response.text}"
            ) from e

    def _extract_keys(self, tickets_metadata):
        """
        Extract the keys from the tickets metadata and return
        a list of release and non-release tickets.
        """
        release, nonrelease = [], []
        for object in tickets_metadata:
            key = object.get("ticket")
            pr_url = object.get("pr_url")

            if re.search(RELEASE_TICKET_PATTERN, key):
                release.append({"key": key, "pr_url": pr_url})
            elif re.search(NON_RELEASE_TICKET_PATTERN, key):
                nonrelease.append({"key": key, "pr_url": pr_url})
            else:
                self.logger.warning(
                    f"Ticket {key} does not match expected patterns, skipping"
                )
        return release, nonrelease

    def _get_ticket_data(self, key):
        """
        Get the ticket metadata from the JIRA API.
        Returns the ticket metadata as a JSON object. Raises
        an error if the response is not a valid JSON object or
        if the HTTP request fails.
        """
        try:
            response = self._call_jira_api(f"issue/{key}", "GET")
            return response.json()
        except JiraHTTPError as e:
            raise JiraHTTPError(f"Failed to fetch ticket data for {key}: {e}") from e
        except json.JSONDecodeError as e:
            raise JiraJSONError(
                f"Failed to parse JSON response for ticket {key}: {e}"
            ) from e

    def _check_if_closed(self, ticket_data):
        """
        Check if the ticket is in a closed state. It will return a
        boolean value True if the ticket is closed, False otherwise.
        """
        return (
            ticket_data.get("fields", {}).get("status", {}).get("name", "").lower()
            == CLOSED_STATUS
        )

    def _check_if_release_pending(self, ticket_data):
        """
        Check if the ticket is in release pending state. It will return a
        boolean value True if the ticket is release pending, False
        otherwise.
        """
        return (
            ticket_data.get("fields", {}).get("status", {}).get("name", "").lower()
            == RELEASE_PENDING_STATUS
        )

    def _check_source_label(self, ticket_data, source):
        """
        Check if the source label is present in the ticket. It will return
        a boolean value True if the source label is present, False otherwise.
        """
        labels = ticket_data.get("fields", {}).get("labels", []) or []
        return source in labels

    def _apply_label_change(self, key, remove, add):
        """
        Apply the label change to the ticket. It will remove the source
        label and add the destination label. Raises an error if the
        HTTP request fails.
        """
        if self.args.dry_run == "true":
            self.logger.info(
                "Running in dry run mode label change would have been applied for "
                f"{key}: - {remove} + {add}"
            )
            return

        labels_update = []
        if remove:
            labels_update.append({"remove": remove})
        if add:
            labels_update.append({"add": add})

        payload = {"update": {"labels": labels_update}}
        try:
            self._call_jira_api(f"issue/{key}", "PUT", payload)
            self.logger.info(f"Label change applied for {key}: - {remove} + {add}")
        except JiraHTTPError as e:
            raise JiraHTTPError(
                f"Failed to apply label change for ticket {key}: {e}"
            ) from e

    def _add_comment(self, key, source, destination, pr_url=None):
        """
        Add a comment to the ticket. If a PR URL is provided, it will
        add the PR URL to the comment. Otherwise, it will add a
        comment with only the source and destination.
        Raises an error if the HTTP request fails.
        """
        if pr_url:
            comment_text = (
                f"The PR linked to this ticket has been promoted from "
                f"{source} to {destination} in the release-service-catalog "
                f"repository. PR: {pr_url}"
            )
        else:
            comment_text = (
                f"The ticket has been promoted from {source} to {destination} "
                f"in the release-service-catalog repository."
            )

        payload = {"body": comment_text}
        if self.args.dry_run == "true":
            self.logger.info(
                f"Running in dry run mode comment would have been added for "
                f"{key}: {comment_text}"
            )
            return
        try:
            self._call_jira_api(f"issue/{key}/comment", "POST", payload)
            self.logger.info(f"Comment added for {key}: {comment_text}")
        except JiraHTTPError as e:
            raise JiraHTTPError(f"Failed to add comment to ticket {key}: {e}") from e

    def _process_release_issue(self, data, source, destination):
        """
        Process a single RELEASE ticket. It will check if the ticket is in a
        closed state, if the ticket is in a release pending state, and if
        the source label is present. If the ticket is not in a release pending
        state or the source label is not present, it will add a comment.
        It will skip if the ticket is in a closed state.
        """
        key = data["key"]
        pr_url = data.get("pr_url")
        ticket_data = self._get_ticket_data(key)

        if self._check_if_closed(ticket_data):
            self.logger.info(f"Skipping {key} since it is closed")
            return

        if not self._check_if_release_pending(ticket_data):
            self.logger.info(
                f"Skipping {key} label change since it is not in release pending state. "
                "A comment will be added instead."
            )
            self._add_comment(key, source, destination, pr_url)
            return

        if not self._check_source_label(ticket_data, source):
            self.logger.info(
                f"Skipping {key} label change since {source} label not found. "
                "A comment will be added instead."
            )
            self._add_comment(key, source, destination, pr_url)
            return

        self._apply_label_change(key, source, destination)
        self._add_comment(key, source, destination, pr_url)

    def _process_non_release_issue(self, data, source, destination):
        """
        Process a single non-RELEASE ticket. It will check if the ticket is in
        a closed state. If the ticket is closed, it will skip the ticket.
        Otherwise, it will add a comment.
        """
        key = data["key"]
        pr_url = data.get("pr_url")
        ticket_data = self._get_ticket_data(key)

        if self._check_if_closed(ticket_data):
            self.logger.info(f"Skipping {key} since it is closed")
            return

        self._add_comment(key, source, destination, pr_url)

    def process_tickets(self, data):
        """
        Process the tickets metadata. It will extract the keys from the
        tickets metadata, and then process the release and non-release
        tickets.
        """
        source, destination = self.args.promotion_type.split("-to-")
        release_tickets, non_release_tickets = self._extract_keys(data)

        if not release_tickets and not non_release_tickets:
            self.logger.info("No tickets found in tickets metadata, skipping.")
            return

        if release_tickets:
            self.logger.info(
                f"Found {len(release_tickets)} RELEASE tickets: "
                f"{[t['key'] for t in release_tickets]}"
            )
            for ticket in release_tickets:
                self._process_release_issue(ticket, source, destination)
        else:
            self.logger.info("No RELEASE tickets found in tickets, skipping.")

        if non_release_tickets:
            self.logger.info(
                f"Found {len(non_release_tickets)} non-RELEASE tickets: "
                f"{[t['key'] for t in non_release_tickets]}"
            )
            for ticket in non_release_tickets:
                self._process_non_release_issue(ticket, source, destination)
        else:
            self.logger.info("No non-RELEASE tickets found in tickets, skipping.")


def setup_logging():
    logger = logging.getLogger("jira")
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s")
    )
    if not logger.handlers:
        logger.addHandler(handler)
    return logger


def parse_args():
    """
    Parse command line arguments and returns the arguments as a
    namespace object.
    """
    parser = argparse.ArgumentParser(
        prog="jira_ci",
        description=(
            "Promotes JIRA tickets information from one stage to another "
            "in the release process"
        ),
    )
    parser.add_argument(
        "--jira_url",
        required=True,
        help="The URL of the JIRA instance",
    )
    parser.add_argument(
        "--promotion_type",
        required=True,
        choices=["development-to-staging", "staging-to-production"],
        help=(
            "The type of promotion to perform from development to "
            "staging or staging to production"
        ),
    )
    parser.add_argument(
        "--metadata_file",
        required=True,
        help="The file containing the metadata for the tickets in JSON format",
    )
    parser.add_argument(
        "--dry_run",
        type=str,
        default="false",
        choices=["true", "false"],
        help=(
            "Run the script in dry run mode GET API call will be made but "
            "no changes will be applied"
        ),
    )
    return parser.parse_args()


def load_metadata(metadata_file):
    """
    Load the metadata from the JSON file and return it as a
    JSON object. Raises an error if the file is not found
    or if the JSON is invalid.
    """
    try:
        with open(metadata_file, "r") as f:
            data = json.load(f)
        if not all(isinstance(obj, dict) and "ticket" in obj for obj in data):
            raise JiraJSONError(
                "Invalid metadata file. All objects must have a 'ticket' key."
            )
        return data
    except FileNotFoundError as e:
        raise JiraError(f"File {metadata_file} not found: {e}") from e
    except json.JSONDecodeError as e:
        raise JiraJSONError(f"Invalid JSON in file {metadata_file}: {e}") from e


def main():
    logger = setup_logging()
    try:
        args = parse_args()
        jira_client = JiraClient(logger, args)
        data = load_metadata(args.metadata_file)
        jira_client.process_tickets(data)
    except JiraError as e:
        logger.error(e)
        exit(1)


if __name__ == "__main__":
    main()

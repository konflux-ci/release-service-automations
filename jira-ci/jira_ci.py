import argparse
import logging
import os
import requests
import json
import re

RELEASE_TICKET_PATTERN = r"RELEASE-\d+"
NON_RELEASE_TICKET_PATTERN = r"[A-Z]+-\d+"
CLOSED_STATUS = "closed"
RELEASE_PENDING_STATUS = "release pending"


class JiraClient:
    def __init__(self, logger, args):
        self.logger = logger
        self.args = args
        self.__load_env()
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.token}",
            }
        )

    def __load_env(self):
        """Load the environment variables"""
        self.token = os.getenv("JIRA_TOKEN")
        if not self.token:
            raise Exception("JIRA_TOKEN is not set as env variable")

    def __call_jira_api(self, endpoint, method, data=None):
        """Makes a request to the JIRA API"""
        try:
            url = f"{self.args.jira_url}/rest/api/2/{endpoint}"
            response = self.session.request(method, url, json=data)
            response.raise_for_status()
            return response
        except requests.exceptions.HTTPError as e:
            raise requests.exceptions.HTTPError(
                f"HTTP error calling JIRA API: {e.response.status_code} "
                f"text: {e.response.text}"
            )

    def __extract_keys(self, tickets_metadata):
        """Extract the keys from the tickets metadata"""
        release, nonrelease = [], []
        for object in tickets_metadata:
            if object.get("ticket"):
                key = object.get("ticket")
                pr_url = object.get("pr_url")

                if re.search(RELEASE_TICKET_PATTERN, key):
                    release.append({"key": key, "pr_url": pr_url})
                elif re.search(NON_RELEASE_TICKET_PATTERN, key):
                    nonrelease.append({"key": key, "pr_url": pr_url})
            else:
                self.logger.warning(
                    f"Ticket {object.get('key')} is not a valid JIRA ticket skipping"
                )
        return release, nonrelease

    def __get_ticket_data(self, key):
        """Get the data for the ticket"""
        try:
            response = self.__call_jira_api(f"issue/{key}", "GET")
            return response.json()
        except Exception as e:
            raise Exception(f"Failed to get ticket data {e}")

    def __check_if_closed(self, ticket_data):
        """Check if the ticket is closed status will return a boolean"""
        return (
            ticket_data.get("fields", {}).get("status", {}).get("name", "").lower()
            == CLOSED_STATUS
        )

    def __check_if_release_pending(self, ticket_data):
        """Check if the ticket is in release pending status will return a boolean"""
        return (
            ticket_data.get("fields", {}).get("status", {}).get("name", "").lower()
            == RELEASE_PENDING_STATUS
        )

    def __check_source_label(self, ticket_data, source):
        """Check if the source label is present in the ticket will returns a boolean"""
        labels = ticket_data.get("fields", {}).get("labels", []) or []
        return source in labels

    def __apply_label_change(self, key, remove, add):
        """Apply the label change to the ticket with the given key
        and the labels to remove and add"""
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
            response = self.__call_jira_api(f"issue/{key}", "PUT", payload)
            response.raise_for_status()
            self.logger.info(f"Label change applied for {key}: - {remove} + {add}")
        except Exception as e:
            raise Exception(f"Failed to apply label change {e}")

    def __add_comment(self, key, source, destination, pr_url=None):
        """Add a comment to the ticket with the given key,
        source, destination and pr_url if provided"""
        if pr_url:
            comment_text = (
                f"The PR linked to this ticket has been promoted from "
                f"{source} to {destination} in the release-service-catalog "
                f" repository. PR: {pr_url}"
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
            self.__call_jira_api(f"issue/{key}/comment", "POST", payload)
            self.logger.info(f"Comment added for {key}: {comment_text}")
        except Exception as e:
            raise Exception(f"Failed to add comment {e}")

    def __process_release_issue(self, data, source, destination):
        """Process a single RELEASE ticket"""
        try:
            key = data["key"]
            pr_url = data.get("pr_url")
            ticket_data = self.__get_ticket_data(key)

            if self.__check_if_closed(ticket_data):
                self.logger.info(f"Skipping {key} since it is closed")
                return

            if not self.__check_if_release_pending(ticket_data):
                self.logger.info(
                    f"Skipping {key} label change since it is not in release pending state. "
                    "A comment will be added instead."
                )
                self.__add_comment(key, source, destination, pr_url)
                return

            if not self.__check_source_label(ticket_data, source):
                self.logger.info(
                    f"Skipping {key} label change since {source} label not found. "
                    "A comment will be added instead."
                )
                self.__add_comment(key, source, destination, pr_url)
                return

            self.__apply_label_change(key, source, destination)
            self.__add_comment(key, source, destination, pr_url)
        except Exception as e:
            raise Exception(f"Error processing RELEASE ticket {key}: {e}")

    def __process_non_release_issue(self, data, source, destination):
        """Process a single non-RELEASE ticket"""
        key = data["key"]
        pr_url = data.get("pr_url")

        try:
            ticket_data = self.__get_ticket_data(key)

            if self.__check_if_closed(ticket_data):
                self.logger.info(f"Skipping {key} since it is closed")
                return

            self.__add_comment(key, source, destination, pr_url)
        except Exception as e:
            raise Exception(f"Error processing non-RELEASE ticket {key}: {e}")

    def process_tickets(self, data):
        """Process the tickets metadata"""
        source, destination = self.args.promotion_type.split("-to-")
        release_tickets, non_release_tickets = self.__extract_keys(data)

        if not release_tickets and not non_release_tickets:
            self.logger.info("No tickets found in tickets metadata, skipping.")
            return

        if release_tickets:
            self.logger.info(
                f"Found {len(release_tickets)} RELEASE tickets: "
                f"{[t['key'] for t in release_tickets]}"
            )
            for ticket in release_tickets:
                self.__process_release_issue(ticket, source, destination)
        else:
            self.logger.info("No RELEASE tickets found in tickets, skipping.")

        if non_release_tickets:
            self.logger.info(
                f"Found {len(non_release_tickets)} non-RELEASE tickets: "
                f"{[t['key'] for t in non_release_tickets]}"
            )
            for ticket in non_release_tickets:
                self.__process_non_release_issue(ticket, source, destination)
        else:
            self.logger.info("No non-RELEASE tickets found in tickets, skipping.")


def setup_logging():
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s")
    )
    if not logger.handlers:
        logger.addHandler(handler)
    return logger


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        prog="jira_promoter",
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
            "Run the script in dry run mode get API call will be made but "
            "no changes will be applied"
        ),
    )

    return parser.parse_args()


def main():
    logger = setup_logging()
    try:
        args = parse_args()
        jira_client = JiraClient(logger.getChild("Jira"), args)
        with open(args.metadata_file, "r") as f:
            data = json.load(f)
        jira_client.process_tickets(data)
    except Exception as e:
        logger.error(e)
        exit(1)


if __name__ == "__main__":
    main()

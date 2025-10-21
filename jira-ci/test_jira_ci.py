import pytest
import os
import json
from unittest.mock import Mock, patch, mock_open
import requests

import jira_ci


class TestMainFunction:
    """
    Test cases for the main function that loads the metadata,
    parses the arguments, and call the JiraClient
    to process the tickets.
    """

    def test_parse_args_valid(self):
        """
        Test parse_args function with valid arguments
        and returns the arguments as an object.
        """
        test_args = [
            "--jira_url",
            "https://test-jira.com",
            "--promotion_type",
            "development-to-staging",
            "--metadata_file",
            "test.json",
        ]

        with patch("sys.argv", ["jira_ci.py"] + test_args):
            args = jira_ci.parse_args()

            assert args.jira_url == "https://test-jira.com"
            assert args.promotion_type == "development-to-staging"
            assert args.metadata_file == "test.json"
            assert args.dry_run == "false"

    def test_parse_args_invalid_promotion_type(self):
        """
        Test parse_args function raises SystemExit with
        invalid promotion type and raises an error.
        """
        test_args = [
            "--jira_url",
            "https://test-jira.com",
            "--promotion_type",
            "invalid-type",
            "--metadata_file",
            "test.json",
        ]

        with patch("sys.argv", ["jira_ci.py"] + test_args):
            with pytest.raises(SystemExit):
                jira_ci.parse_args()

    def test_load_metadata_success(self):
        """
        Test load_metadata function loads the metadata successfully.
        """
        with patch("builtins.open", mock_open(read_data='[{"ticket": "TEST-123"}]')):
            with patch("jira_ci.json.load") as mock_load:
                mock_load.return_value = [{"ticket": "TEST-123"}]
                data = jira_ci.load_metadata("test.json")
                assert data == [{"ticket": "TEST-123"}]

    def test_load_metadata_invalid_json(self):
        """
        Test load_metadata function raises an exception when the JSON is invalid.
        """
        with patch("builtins.open", mock_open(read_data="invalid json")):
            with patch("jira_ci.json.load") as mock_load:
                mock_load.side_effect = json.JSONDecodeError("Invalid JSON", "doc", 0)
                with pytest.raises(
                    jira_ci.JiraJSONError,
                    match="Invalid JSON in file test.json: Invalid JSON",
                ):
                    jira_ci.load_metadata("test.json")


class TestJiraClient:
    """
    Test cases for the JiraClient which
    is used to process the tickets.
    """

    @pytest.fixture(autouse=True)
    def setup_method(self):
        """
        Set up test fixtures before each test.
        """
        self.mock_logger = Mock()
        self.mock_args = Mock()
        self.mock_args.jira_url = "https://dummy-jira.com"
        self.mock_args.promotion_type = "development-to-staging"
        self.mock_args.dry_run = "false"

    @patch.dict(os.environ, {"JIRA_TOKEN": "test-token"})
    def test_jira_client_init_success(self):
        """
        Test JiraClient initializes successfully.
        """
        with patch("jira_ci.requests.Session") as mock_session:
            client = jira_ci.JiraClient(self.mock_logger, self.mock_args)

            assert client.logger == self.mock_logger
            assert client.args == self.mock_args
            assert client.token == "test-token"
            mock_session.assert_called_once()

    @patch.dict(os.environ, {"JIRA_TOKEN": "test-token"})
    def test_load_env_success(self):
        """
        Test _load_env method loads the JIRA token successfully.
        """
        with patch("jira_ci.requests.Session"):
            client = jira_ci.JiraClient(self.mock_logger, self.mock_args)
            assert client.token == "test-token"

    @patch.dict(os.environ, {}, clear=True)
    def test_load_env_failure(self):
        """
        Test _load_env method raises an exception when the JIRA token is missing.
        """
        with pytest.raises(
            jira_ci.JiraError, match="'JIRA_TOKEN' is not set as env variable"
        ):
            with patch("jira_ci.requests.Session"):
                jira_ci.JiraClient(self.mock_logger, self.mock_args)

    @patch.dict(os.environ, {"JIRA_TOKEN": "test-token"})
    def test_load_session_success(self):
        """
        Test _load_session method loads the session successfully.
        """
        with patch("jira_ci.requests.Session") as mock_session_class:
            client = jira_ci.JiraClient(self.mock_logger, self.mock_args)
            assert client.session == mock_session_class.return_value
            mock_session_class.assert_called_once()

    @patch.dict(os.environ, {"JIRA_TOKEN": "test-token"})
    def test_call_jira_api_success(self):
        """
        Test _call_jira_api method makes a successful request to the JIRA API.
        """
        with patch("jira_ci.requests.Session") as mock_session_class:
            mock_session = Mock()
            mock_response = Mock()
            mock_response.raise_for_status.return_value = None
            mock_response.json.return_value = {"key": "TEST-123"}
            mock_session.request.return_value = mock_response
            mock_session_class.return_value = mock_session

            client = jira_ci.JiraClient(self.mock_logger, self.mock_args)
            response = client._call_jira_api("issue/TEST-123", "GET")

            assert response == mock_response
            mock_session.request.assert_called_once_with(
                "GET",
                "https://dummy-jira.com/rest/api/2/issue/TEST-123",
                json=None,
                timeout=30,
            )

    @patch.dict(os.environ, {"JIRA_TOKEN": "test-token"})
    def test_call_jira_api_http_error(self):
        """
        Test _call_jira_api method raises an exception when the
        request fails with a HTTP error.
        """
        with patch("jira_ci.requests.Session") as mock_session_class:
            mock_session = Mock()
            mock_response = Mock()
            mock_response.status_code = 404
            mock_response.text = "Not Found"
            mock_error = requests.exceptions.HTTPError()
            mock_error.response = mock_response
            mock_session.request.side_effect = mock_error
            mock_session_class.return_value = mock_session

            client = jira_ci.JiraClient(self.mock_logger, self.mock_args)

            with pytest.raises(
                jira_ci.JiraHTTPError,
                match=(
                    "HTTP error calling JIRA API 404 url: "
                    "https://dummy-jira.com/rest/api/2/issue/TEST-123 message: Not Found"
                ),
            ):
                client._call_jira_api("issue/TEST-123", "GET")

    def test_extract_keys_tickets(self):
        """
        Test _extract_keys method extracts the release
        and non-release tickets from the tickets metadata.
        """
        with patch.dict(os.environ, {"JIRA_TOKEN": "test-token"}):
            with patch("jira_ci.requests.Session"):
                client = jira_ci.JiraClient(self.mock_logger, self.mock_args)

                tickets_metadata = [
                    {"ticket": "RELEASE-123", "pr_url": "https://github.com/test/pr/1"},
                    {"ticket": "RELEASE-456", "pr_url": "https://github.com/test/pr/2"},
                    {"ticket": "OTHER-789"},
                    {"ticket": "INVALID", "pr_url": "https://github.com/test/pr/4"},
                ]

                release, nonrelease = client._extract_keys(tickets_metadata)

                assert len(release) == 2
                assert len(nonrelease) == 1
                assert release[0]["key"] == "RELEASE-123"
                assert release[0]["pr_url"] == "https://github.com/test/pr/1"
                assert release[1]["key"] == "RELEASE-456"
                assert release[1]["pr_url"] == "https://github.com/test/pr/2"
                assert nonrelease[0]["key"] == "OTHER-789"
                assert nonrelease[0]["pr_url"] is None

    def test_extract_keys_no_tickets(self):
        """
        Test _extract_keys method returns empty lists
        when there are no valid release or non-release tickets.
        """
        with patch.dict(os.environ, {"JIRA_TOKEN": "test-token"}):
            with patch("jira_ci.requests.Session"):
                client = jira_ci.JiraClient(self.mock_logger, self.mock_args)

                tickets_metadata = [
                    {"ticket": "INVALID", "pr_url": "https://github.com/test/pr/1"},
                    {"ticket": "", "pr_url": "https://github.com/test/pr/2"},
                ]

                release, nonrelease = client._extract_keys(tickets_metadata)

                assert len(release) == 0
                assert len(nonrelease) == 0

    @patch.dict(os.environ, {"JIRA_TOKEN": "test-token"})
    def test_get_ticket_data_success(self):
        """
        Test _get_ticket_data method makes a successful request to the JIRA API
        and returns the ticket data as a JSON object.
        """
        with patch("jira_ci.requests.Session"):
            client = jira_ci.JiraClient(self.mock_logger, self.mock_args)

            mock_response = Mock()
            mock_response.json.return_value = {
                "key": "TEST-123",
                "fields": {"summary": "Test Issue"},
            }

            with patch.object(client, "_call_jira_api", return_value=mock_response):
                result = client._get_ticket_data("TEST-123")

                assert result == {
                    "key": "TEST-123",
                    "fields": {"summary": "Test Issue"},
                }

    @patch.dict(os.environ, {"JIRA_TOKEN": "test-token"})
    def test_get_ticket_data_failure(self):
        """
        Test _get_ticket_data method raises an exception
        when the response is not a valid JSON object.
        """
        with patch("jira_ci.requests.Session"):
            client = jira_ci.JiraClient(self.mock_logger, self.mock_args)

            mock_response = Mock()
            mock_response.json.side_effect = json.JSONDecodeError(
                "Invalid JSON format", "doc", 0
            )

            with patch.object(client, "_call_jira_api", return_value=mock_response):
                with pytest.raises(
                    jira_ci.JiraJSONError,
                    match=(
                        "Failed to parse JSON response for ticket TEST-123: "
                        "Invalid JSON format: line 1 column 1 \\(char 0\\)"
                    ),
                ):
                    client._get_ticket_data("TEST-123")

    def test_check_if_closed_true(self):
        """
        Test _check_if_closed method returns true for closed ticket state.
        """
        with patch.dict(os.environ, {"JIRA_TOKEN": "test-token"}):
            with patch("jira_ci.requests.Session"):
                client = jira_ci.JiraClient(self.mock_logger, self.mock_args)

                issue_data = {"fields": {"status": {"name": "Closed"}}}

                result = client._check_if_closed(issue_data)
                assert result is True

    def test_check_if_closed_false(self):
        """
        Test _check_if_closed method returns false for non-closed ticket state.
        """
        with patch.dict(os.environ, {"JIRA_TOKEN": "test-token"}):
            with patch("jira_ci.requests.Session"):
                client = jira_ci.JiraClient(self.mock_logger, self.mock_args)

                issue_data = {"fields": {"status": {"name": "Open"}}}

                result = client._check_if_closed(issue_data)
                assert result is False

    def test_check_if_release_pending_true(self):
        """
        Test _check_if_release_pending method returns true for
        release pending ticket state.
        """
        with patch.dict(os.environ, {"JIRA_TOKEN": "test-token"}):
            with patch("jira_ci.requests.Session"):
                client = jira_ci.JiraClient(self.mock_logger, self.mock_args)

                issue_data = {"fields": {"status": {"name": "Release Pending"}}}

                result = client._check_if_release_pending(issue_data)
                assert result is True

    def test_check_if_release_pending_false(self):
        """
        Test _check_if_release_pending method returns false for non-release
        pending ticket state.
        """
        with patch.dict(os.environ, {"JIRA_TOKEN": "test-token"}):
            with patch("jira_ci.requests.Session"):
                client = jira_ci.JiraClient(self.mock_logger, self.mock_args)

                issue_data = {"fields": {"status": {"name": "Open"}}}

                result = client._check_if_release_pending(issue_data)
                assert result is False

    def test_check_source_label_true(self):
        """
        Test _check_source_label method returns true when label
        is present in the ticket data.
        """
        with patch.dict(os.environ, {"JIRA_TOKEN": "test-token"}):
            with patch("jira_ci.requests.Session"):
                client = jira_ci.JiraClient(self.mock_logger, self.mock_args)

                issue_data = {"fields": {"labels": ["development", "staging"]}}

                result = client._check_source_label(issue_data, "development")
                assert result is True

    def test_check_source_label_false(self):
        """
        Test _check_source_label method returns false when label
        is not present in the ticket data.
        """
        with patch.dict(os.environ, {"JIRA_TOKEN": "test-token"}):
            with patch("jira_ci.requests.Session"):
                client = jira_ci.JiraClient(self.mock_logger, self.mock_args)

                issue_data = {"fields": {"labels": ["staging", "production"]}}

                result = client._check_source_label(issue_data, "development")
                assert result is False

    @patch.dict(os.environ, {"JIRA_TOKEN": "test-token"})
    def test_apply_label_change_success(self):
        """
        Test _apply_label_change method makes a successful request
        to the JIRA API to update the ticket labels from source to destination.
        """
        with patch("jira_ci.requests.Session"):
            client = jira_ci.JiraClient(self.mock_logger, self.mock_args)

            mock_response = Mock()
            mock_response.raise_for_status.return_value = None
            with patch.object(
                client, "_call_jira_api", return_value=mock_response
            ) as mock_call:
                client._apply_label_change("TEST-123", "development", "staging")
                mock_call.assert_called_once_with(
                    "issue/TEST-123",
                    "PUT",
                    {
                        "update": {
                            "labels": [{"remove": "development"}, {"add": "staging"}]
                        }
                    },
                )

    @patch.dict(os.environ, {"JIRA_TOKEN": "test-token"})
    def test_apply_label_change_failure(self):
        """
        Test _apply_label_change method raises an exception when
        the request fails with a HTTP error.
        """
        with patch("jira_ci.requests.Session"):
            self.mock_args.dry_run = "false"
            client = jira_ci.JiraClient(self.mock_logger, self.mock_args)

            with patch.object(
                client, "_call_jira_api", side_effect=jira_ci.JiraHTTPError("API Error")
            ):
                with pytest.raises(
                    jira_ci.JiraHTTPError,
                    match="Failed to apply label change for ticket TEST-123: API Error",
                ):
                    client._apply_label_change("TEST-123", "development", "staging")

    @patch.dict(os.environ, {"JIRA_TOKEN": "test-token"})
    def test_add_comment_without_pr_url(self):
        """
        Test _add_comment method without a PR URL provided.
        It should add a comment with only the source and destination.
        """
        with patch("jira_ci.requests.Session"):
            client = jira_ci.JiraClient(self.mock_logger, self.mock_args)

            with patch.object(client, "_call_jira_api") as mock_call:
                client._add_comment("TEST-123", "development", "staging")
                mock_call.assert_called_once_with(
                    "issue/TEST-123/comment",
                    "POST",
                    {
                        "body": (
                            "The ticket has been promoted from development to staging "
                            "in the release-service-catalog repository."
                        )
                    },
                )

    @patch.dict(os.environ, {"JIRA_TOKEN": "test-token"})
    def test_add_comment_success(self):
        """
        Test _add_comment method with a PR URL provided.
        It should add a comment with the PR URL.
        """
        with patch("jira_ci.requests.Session"):
            self.mock_args.dry_run = "false"
            client = jira_ci.JiraClient(self.mock_logger, self.mock_args)

            with patch.object(client, "_call_jira_api") as mock_call:
                client._add_comment(
                    "TEST-123", "development", "staging", "https://github.com/test/pr/1"
                )

                mock_call.assert_called_once()
                mock_call.assert_called_once_with(
                    "issue/TEST-123/comment",
                    "POST",
                    {
                        "body": (
                            "The PR linked to this ticket has been promoted from "
                            "development to staging in the release-service-catalog "
                            "repository. PR: https://github.com/test/pr/1"
                        )
                    },
                )

    @patch.dict(os.environ, {"JIRA_TOKEN": "test-token"})
    def test_add_comment_failure(self):
        """
        Test _add_comment method raises an exception
        when the request fails with a HTTP error.
        """
        with patch("jira_ci.requests.Session"):
            self.mock_args.dry_run = "false"
            client = jira_ci.JiraClient(self.mock_logger, self.mock_args)

            with patch.object(
                client, "_call_jira_api", side_effect=jira_ci.JiraHTTPError("API Error")
            ):
                with pytest.raises(
                    jira_ci.JiraHTTPError,
                    match="Failed to add comment to ticket TEST-123: API Error",
                ):
                    client._add_comment("TEST-123", "development", "staging")

    @patch.dict(os.environ, {"JIRA_TOKEN": "test-token"})
    def test_process_release_issue_closed(self):
        """
        Test _process_release_issue method skips a
        closed release ticket.
        """
        with patch("jira_ci.requests.Session"):
            client = jira_ci.JiraClient(self.mock_logger, self.mock_args)

            issue_data = {"fields": {"status": {"name": "Closed"}}}

            with patch.object(client, "_get_ticket_data", return_value=issue_data):
                with patch.object(client, "_apply_label_change") as mock_apply_label:
                    with patch.object(client, "_add_comment") as mock_add_comment:
                        client._process_release_issue(
                            {"key": "RELEASE-123"}, "development", "staging"
                        )

                        # Check that the label change and comment were not called.
                        mock_apply_label.assert_not_called()
                        mock_add_comment.assert_not_called()

                        self.mock_logger.info.assert_called_with(
                            "Skipping RELEASE-123 since it is closed"
                        )

    @patch.dict(os.environ, {"JIRA_TOKEN": "test-token"})
    def test_process_release_issue_not_release_pending(self):
        """
        Test _process_release_issue method skips label change as
        the ticket is not in release pending state. A comment will
        be added instead.
        """
        with patch("jira_ci.requests.Session"):
            client = jira_ci.JiraClient(self.mock_logger, self.mock_args)

            issue_data = {
                "fields": {"status": {"name": "Open"}, "labels": ["development"]}
            }

            with patch.object(client, "_get_ticket_data", return_value=issue_data):
                with patch.object(client, "_add_comment") as mock_add_comment:
                    with patch.object(
                        client, "_apply_label_change"
                    ) as mock_apply_label:
                        client._process_release_issue(
                            {
                                "key": "RELEASE-123",
                                "pr_url": "https://github.com/test/pr/1",
                            },
                            "development",
                            "staging",
                        )

                        # Check that the label change was not called
                        mock_apply_label.assert_not_called()

                        mock_add_comment.assert_called_once_with(
                            "RELEASE-123",
                            "development",
                            "staging",
                            "https://github.com/test/pr/1",
                        )

    @patch.dict(os.environ, {"JIRA_TOKEN": "test-token"})
    def test_process_release_issue_no_source_label(self):
        """
        Test _process_release_issue method skips label change as
        the ticket does not have the source label. A comment will
        be added instead.
        """
        with patch("jira_ci.requests.Session"):
            client = jira_ci.JiraClient(self.mock_logger, self.mock_args)

            issue_data = {
                "fields": {"status": {"name": "Release Pending"}, "labels": ["staging"]}
            }

            with patch.object(client, "_get_ticket_data", return_value=issue_data):
                with patch.object(client, "_add_comment") as mock_add_comment:
                    with patch.object(
                        client, "_apply_label_change"
                    ) as mock_apply_label:
                        client._process_release_issue(
                            {"key": "RELEASE-123"}, "development", "staging"
                        )

                        # Check that label change was not called
                        mock_apply_label.assert_not_called()

                        mock_add_comment.assert_called_once_with(
                            "RELEASE-123", "development", "staging", None
                        )

                        self.mock_logger.info.assert_called_with(
                            "Skipping RELEASE-123 label change since development "
                            "label not found. A comment will be added instead."
                        )

    @patch.dict(os.environ, {"JIRA_TOKEN": "test-token"})
    def test_process_release_issue_success(self):
        """
        Test _process_release_issue method processes a release ticket successfully.
        It should apply the label change and add a comment with the PR URL.
        """
        with patch("jira_ci.requests.Session"):
            client = jira_ci.JiraClient(self.mock_logger, self.mock_args)

            issue_data = {
                "fields": {
                    "status": {"name": "Release Pending"},
                    "labels": ["development"],
                }
            }

            with patch.object(client, "_get_ticket_data", return_value=issue_data):
                with patch.object(client, "_apply_label_change") as mock_apply_label:
                    with patch.object(client, "_add_comment") as mock_add_comment:
                        client._process_release_issue(
                            {
                                "key": "RELEASE-123",
                                "pr_url": "https://github.com/test/pr/1",
                            },
                            "development",
                            "staging",
                        )

                        mock_apply_label.assert_called_once_with(
                            "RELEASE-123", "development", "staging"
                        )
                        mock_add_comment.assert_called_once_with(
                            "RELEASE-123",
                            "development",
                            "staging",
                            "https://github.com/test/pr/1",
                        )

    @patch.dict(os.environ, {"JIRA_TOKEN": "test-token"})
    def test_process_non_release_issue_closed(self):
        """
        Test _process_non_release_issue method skips a
        closed non-release ticket.
        """
        with patch("jira_ci.requests.Session"):
            client = jira_ci.JiraClient(self.mock_logger, self.mock_args)

            issue_data = {"fields": {"status": {"name": "Closed"}}}

            with patch.object(client, "_get_ticket_data", return_value=issue_data):
                with patch.object(client, "_add_comment") as mock_add_comment:
                    client._process_non_release_issue(
                        {"key": "OTHER-123"}, "development", "staging"
                    )

                    # Check that no comment was added
                    mock_add_comment.assert_not_called()

                    self.mock_logger.info.assert_called_with(
                        "Skipping OTHER-123 since it is closed"
                    )

    @patch.dict(os.environ, {"JIRA_TOKEN": "test-token"})
    def test_process_non_release_issue_success(self):
        """
        Test _process_non_release_issue method processes a non-release ticket successfully.
        It should add a comment with the PR URL.
        """
        with patch("jira_ci.requests.Session"):
            client = jira_ci.JiraClient(self.mock_logger, self.mock_args)

            issue_data = {"fields": {"status": {"name": "Open"}}}

            with patch.object(client, "_get_ticket_data", return_value=issue_data):
                with patch.object(client, "_add_comment") as mock_add_comment:
                    client._process_non_release_issue(
                        {"key": "OTHER-123", "pr_url": "https://github.com/test/pr/1"},
                        "development",
                        "staging",
                    )

                    mock_add_comment.assert_called_once_with(
                        "OTHER-123",
                        "development",
                        "staging",
                        "https://github.com/test/pr/1",
                    )

    @patch.dict(os.environ, {"JIRA_TOKEN": "test-token"})
    def test_process_tickets_no_tickets(self):
        """
        Test process_tickets method with no tickets in metadata.
        It should log a message and return.
        """
        with patch("jira_ci.requests.Session"):
            client = jira_ci.JiraClient(self.mock_logger, self.mock_args)

            with patch.object(client, "_extract_keys", return_value=([], [])):
                client.process_tickets([])

                self.mock_logger.info.assert_called_with(
                    "No tickets found in tickets metadata, skipping."
                )

    @patch.dict(os.environ, {"JIRA_TOKEN": "test-token"})
    def test_process_tickets_with_release_tickets(self):
        """
        Test process_tickets method with release and non-release tickets.
        It should process the release and non-release tickets.
        """
        with patch("jira_ci.requests.Session"):
            client = jira_ci.JiraClient(self.mock_logger, self.mock_args)

            release_tickets = [
                {"key": "RELEASE-123", "pr_url": "https://github.com/test/pr/1"}
            ]
            non_release_tickets = [
                {"key": "OTHER-123", "pr_url": "https://github.com/test/pr/2"}
            ]

            with patch.object(
                client,
                "_extract_keys",
                return_value=(release_tickets, non_release_tickets),
            ):
                with patch.object(
                    client, "_process_release_issue"
                ) as mock_process_release:
                    with patch.object(
                        client, "_process_non_release_issue"
                    ) as mock_process_non_release:
                        client.process_tickets([])

                        mock_process_release.assert_called_once_with(
                            release_tickets[0], "development", "staging"
                        )
                        mock_process_non_release.assert_called_once_with(
                            non_release_tickets[0], "development", "staging"
                        )

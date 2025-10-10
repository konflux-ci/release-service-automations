import pytest
import os
from unittest.mock import Mock, patch
import requests

import jira_ci


class TestMainFunction:
    """Test cases for main function"""

    def test_parse_args_valid(self):
        """Test parse_args function with valid arguments"""
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
        """Test parse_args function with invalid promotion type"""
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


class TestJiraClient:
    """Test cases for JiraClient class"""

    @pytest.fixture(autouse=True)
    def setup_method(self):
        """Set up test fixtures before each test method"""
        self.mock_logger = Mock()
        self.mock_args = Mock()
        self.mock_args.jira_url = "https://test-jira.com"
        self.mock_args.promotion_type = "development-to-staging"
        self.mock_args.dry_run = False

    @patch.dict(os.environ, {"JIRA_TOKEN": "test-token"})
    def test_jira_client_init_success(self):
        """Test JiraClient init with valid token"""
        with patch("jira_ci.requests.Session") as mock_session:
            client = jira_ci.JiraClient(self.mock_logger, self.mock_args)

            assert client.logger == self.mock_logger
            assert client.args == self.mock_args
            assert client.token == "test-token"
            mock_session.assert_called_once()

    @patch.dict(os.environ, {}, clear=True)
    def test_jira_client_init_no_token(self):
        """Test JiraClient init without token raises exception"""
        with pytest.raises(Exception, match="JIRA_TOKEN is not set as env variable"):
            jira_ci.JiraClient(self.mock_logger, self.mock_args)

    @patch.dict(os.environ, {"JIRA_TOKEN": "test-token"})
    def test_load_env_success(self):
        """Test __load_env method loads token successfully"""
        with patch("jira_ci.requests.Session"):
            client = jira_ci.JiraClient(self.mock_logger, self.mock_args)
            assert client.token == "test-token"

    @patch.dict(os.environ, {}, clear=True)
    def test_load_env_failure(self):
        """Test __load_env method raises exception when token is missing"""
        with pytest.raises(Exception, match="JIRA_TOKEN is not set as env variable"):
            with patch("jira_ci.requests.Session"):
                jira_ci.JiraClient(self.mock_logger, self.mock_args)

    @patch.dict(os.environ, {"JIRA_TOKEN": "test-token"})
    def test_call_jira_api_success(self):
        """Test __call_jira_api method with successful response"""
        with patch("jira_ci.requests.Session") as mock_session_class:
            mock_session = Mock()
            mock_response = Mock()
            mock_response.raise_for_status.return_value = None
            mock_response.json.return_value = {"key": "TEST-123"}
            mock_session.request.return_value = mock_response
            mock_session_class.return_value = mock_session

            client = jira_ci.JiraClient(self.mock_logger, self.mock_args)
            response = client._JiraClient__call_jira_api("issue/TEST-123", "GET")

            assert response == mock_response
            mock_session.request.assert_called_once_with(
                "GET", "https://test-jira.com/rest/api/2/issue/TEST-123", json=None
            )

    @patch.dict(os.environ, {"JIRA_TOKEN": "test-token"})
    def test_call_jira_api_http_error(self):
        """Test __call_jira_api method with HTTP error"""
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
                Exception, match="HTTP error calling JIRA API: 404 text: Not Found"
            ):
                client._JiraClient__call_jira_api("issue/TEST-123", "GET")

    def test_extract_keys_tickets(self):
        """Test __extract_keys method with tickets"""
        with patch.dict(os.environ, {"JIRA_TOKEN": "test-token"}):
            with patch("jira_ci.requests.Session"):
                client = jira_ci.JiraClient(self.mock_logger, self.mock_args)

                tickets_metadata = [
                    {"ticket": "RELEASE-123", "pr_url": "https://github.com/test/pr/1"},
                    {"ticket": "RELEASE-456", "pr_url": "https://github.com/test/pr/2"},
                    {"ticket": "OTHER-789"},
                    {"ticket": "INVALID", "pr_url": "https://github.com/test/pr/4"},
                ]

                release, nonrelease = client._JiraClient__extract_keys(tickets_metadata)

                assert len(release) == 2
                assert len(nonrelease) == 1
                assert release[0]["key"] == "RELEASE-123"
                assert release[0]["pr_url"] == "https://github.com/test/pr/1"
                assert release[1]["key"] == "RELEASE-456"
                assert release[1]["pr_url"] == "https://github.com/test/pr/2"
                assert nonrelease[0]["key"] == "OTHER-789"
                assert nonrelease[0]["pr_url"] is None

    def test_extract_keys_no_tickets(self):
        """Test __extract_keys method with no valid tickets"""
        with patch.dict(os.environ, {"JIRA_TOKEN": "test-token"}):
            with patch("jira_ci.requests.Session"):
                client = jira_ci.JiraClient(self.mock_logger, self.mock_args)

                tickets_metadata = [
                    {"ticket": "INVALID", "pr_url": "https://github.com/test/pr/1"},
                    {"ticket": "", "pr_url": "https://github.com/test/pr/2"},
                ]

                release, nonrelease = client._JiraClient__extract_keys(tickets_metadata)

                assert len(release) == 0
                assert len(nonrelease) == 0

    @patch.dict(os.environ, {"JIRA_TOKEN": "test-token"})
    def test_get_ticket_data_success(self):
        """Test __get_ticket_data method with successful response"""
        with patch("jira_ci.requests.Session"):
            client = jira_ci.JiraClient(self.mock_logger, self.mock_args)

            mock_response = Mock()
            mock_response.json.return_value = {
                "key": "TEST-123",
                "fields": {"summary": "Test Issue"},
            }

            with patch.object(
                client, "_JiraClient__call_jira_api", return_value=mock_response
            ):
                result = client._JiraClient__get_ticket_data("TEST-123")

                assert result == {
                    "key": "TEST-123",
                    "fields": {"summary": "Test Issue"},
                }

    @patch.dict(os.environ, {"JIRA_TOKEN": "test-token"})
    def test_get_ticket_data_failure(self):
        """Test __get_ticket_data method with API failure"""
        with patch("jira_ci.requests.Session"):
            client = jira_ci.JiraClient(self.mock_logger, self.mock_args)

            with patch.object(
                client, "_JiraClient__call_jira_api", side_effect=Exception("API Error")
            ):
                with pytest.raises(
                    Exception, match="Failed to get ticket data API Error"
                ):
                    client._JiraClient__get_ticket_data("TEST-123")

    def test_check_if_closed_true(self):
        """Test __check_if_closed method returns True for closed ticket status"""
        with patch.dict(os.environ, {"JIRA_TOKEN": "test-token"}):
            with patch("jira_ci.requests.Session"):
                client = jira_ci.JiraClient(self.mock_logger, self.mock_args)

                issue_data = {"fields": {"status": {"name": "Closed"}}}

                result = client._JiraClient__check_if_closed(issue_data)
                assert result is True

    def test_check_if_closed_false(self):
        """Test __check_if_closed method returns False for non-closed ticket status"""
        with patch.dict(os.environ, {"JIRA_TOKEN": "test-token"}):
            with patch("jira_ci.requests.Session"):
                client = jira_ci.JiraClient(self.mock_logger, self.mock_args)

                issue_data = {"fields": {"status": {"name": "Open"}}}

                result = client._JiraClient__check_if_closed(issue_data)
                assert result is False

    def test_check_if_release_pending_true(self):
        """Test __check_if_release_pending method returns True for
        release pending ticket status"""
        with patch.dict(os.environ, {"JIRA_TOKEN": "test-token"}):
            with patch("jira_ci.requests.Session"):
                client = jira_ci.JiraClient(self.mock_logger, self.mock_args)

                issue_data = {"fields": {"status": {"name": "Release Pending"}}}

                result = client._JiraClient__check_if_release_pending(issue_data)
                assert result is True

    def test_check_if_release_pending_false(self):
        """Test __check_if_release_pending method returns False for non-release
        pending ticket status"""
        with patch.dict(os.environ, {"JIRA_TOKEN": "test-token"}):
            with patch("jira_ci.requests.Session"):
                client = jira_ci.JiraClient(self.mock_logger, self.mock_args)

                issue_data = {"fields": {"status": {"name": "Open"}}}

                result = client._JiraClient__check_if_release_pending(issue_data)
                assert result is False

    def test_check_source_label_true(self):
        """Test __check_source_label method returns True when label is present in ticket"""
        with patch.dict(os.environ, {"JIRA_TOKEN": "test-token"}):
            with patch("jira_ci.requests.Session"):
                client = jira_ci.JiraClient(self.mock_logger, self.mock_args)

                issue_data = {"fields": {"labels": ["development", "staging"]}}

                result = client._JiraClient__check_source_label(
                    issue_data, "development"
                )
                assert result is True

    def test_check_source_label_false(self):
        """Test __check_source_label method returns False when label
        is not present in ticket"""
        with patch.dict(os.environ, {"JIRA_TOKEN": "test-token"}):
            with patch("jira_ci.requests.Session"):
                client = jira_ci.JiraClient(self.mock_logger, self.mock_args)

                issue_data = {"fields": {"labels": ["staging", "production"]}}

                result = client._JiraClient__check_source_label(
                    issue_data, "development"
                )
                assert result is False

    @patch.dict(os.environ, {"JIRA_TOKEN": "test-token"})
    def test_apply_label_change_success(self):
        """Test __apply_label_change method with successful ticket update"""
        with patch("jira_ci.requests.Session"):
            client = jira_ci.JiraClient(self.mock_logger, self.mock_args)

            mock_response = Mock()
            mock_response.raise_for_status.return_value = None

            with patch.object(
                client, "_JiraClient__call_jira_api", return_value=mock_response
            ):
                client._JiraClient__apply_label_change(
                    "TEST-123", "development", "staging"
                )

                self.mock_logger.info.assert_called_with(
                    "Label change applied for TEST-123: - development + staging"
                )

    @patch.dict(os.environ, {"JIRA_TOKEN": "test-token"})
    def test_apply_label_change_failure(self):
        """Test __apply_label_change method with API failure for ticket"""
        with patch("jira_ci.requests.Session"):
            self.mock_args.dry_run = False
            client = jira_ci.JiraClient(self.mock_logger, self.mock_args)

            with patch.object(
                client, "_JiraClient__call_jira_api", side_effect=Exception("API Error")
            ):
                with pytest.raises(
                    Exception, match="Failed to apply label change API Error"
                ):
                    client._JiraClient__apply_label_change(
                        "TEST-123", "development", "staging"
                    )

    @patch.dict(os.environ, {"JIRA_TOKEN": "test-token"})
    def test_add_comment_without_pr_url(self):
        """Test __add_comment method without PR URL for ticket"""
        with patch("jira_ci.requests.Session"):
            client = jira_ci.JiraClient(self.mock_logger, self.mock_args)

            with patch.object(client, "_JiraClient__call_jira_api") as mock_call:
                client._JiraClient__add_comment("TEST-123", "development", "staging")

                mock_call.assert_called_once()
                call_args = mock_call.call_args
                assert call_args[0][0] == "issue/TEST-123/comment"
                assert call_args[0][1] == "POST"
                assert (
                    "The ticket has been promoted from development to staging"
                    in call_args[0][2]["body"]
                )

    @patch.dict(os.environ, {"JIRA_TOKEN": "test-token"})
    def test_add_comment_success(self):
        """Test __add_comment method with successful comment addition to ticket"""
        with patch("jira_ci.requests.Session"):
            self.mock_args.dry_run = False
            client = jira_ci.JiraClient(self.mock_logger, self.mock_args)

            with patch.object(client, "_JiraClient__call_jira_api") as mock_call:
                client._JiraClient__add_comment(
                    "TEST-123", "development", "staging", "https://github.com/test/pr/1"
                )

                mock_call.assert_called_once()
                call_args = mock_call.call_args
                assert call_args[0][0] == "issue/TEST-123/comment"
                assert call_args[0][1] == "POST"
                assert "PR: https://github.com/test/pr/1" in call_args[0][2]["body"]

    @patch.dict(os.environ, {"JIRA_TOKEN": "test-token"})
    def test_add_comment_failure(self):
        """Test __add_comment method with API failure for ticket"""
        with patch("jira_ci.requests.Session"):
            self.mock_args.dry_run = False
            client = jira_ci.JiraClient(self.mock_logger, self.mock_args)

            with patch.object(
                client, "_JiraClient__call_jira_api", side_effect=Exception("API Error")
            ):
                with pytest.raises(Exception, match="Failed to add comment API Error"):
                    client._JiraClient__add_comment(
                        "TEST-123", "development", "staging"
                    )

    @patch.dict(os.environ, {"JIRA_TOKEN": "test-token"})
    def test_process_release_tickets_closed(self):
        """Test __process_release_tickets method skips closed tickets"""
        with patch("jira_ci.requests.Session"):
            client = jira_ci.JiraClient(self.mock_logger, self.mock_args)

            issue_data = {"fields": {"status": {"name": "Closed"}}}

            with patch.object(
                client, "_JiraClient__get_ticket_data", return_value=issue_data
            ):
                client._JiraClient__process_release_issue(
                    {"key": "RELEASE-123"}, "development", "staging"
                )

                self.mock_logger.info.assert_called_with(
                    "Skipping RELEASE-123 since it is closed"
                )

    @patch.dict(os.environ, {"JIRA_TOKEN": "test-token"})
    def test_process_release_tickets_not_release_pending(self):
        """Test __process_release_tickets method adds comment for non-release
        pending tickets"""
        with patch("jira_ci.requests.Session"):
            client = jira_ci.JiraClient(self.mock_logger, self.mock_args)

            issue_data = {
                "fields": {"status": {"name": "Open"}, "labels": ["development"]}
            }

            with patch.object(
                client, "_JiraClient__get_ticket_data", return_value=issue_data
            ):
                with patch.object(
                    client, "_JiraClient__add_comment"
                ) as mock_add_comment:
                    client._JiraClient__process_release_issue(
                        {
                            "key": "RELEASE-123",
                            "pr_url": "https://github.com/test/pr/1",
                        },
                        "development",
                        "staging",
                    )

                    mock_add_comment.assert_called_once_with(
                        "RELEASE-123",
                        "development",
                        "staging",
                        "https://github.com/test/pr/1",
                    )

    @patch.dict(os.environ, {"JIRA_TOKEN": "test-token"})
    def test_process_release_ticket_no_source_label(self):
        """Test __process_release_issue method adds comment for tickets without source label"""
        with patch("jira_ci.requests.Session"):
            client = jira_ci.JiraClient(self.mock_logger, self.mock_args)

            issue_data = {
                "fields": {"status": {"name": "Release Pending"}, "labels": ["staging"]}
            }

            with patch.object(
                client, "_JiraClient__get_ticket_data", return_value=issue_data
            ):
                with patch.object(
                    client, "_JiraClient__add_comment"
                ) as mock_add_comment:
                    client._JiraClient__process_release_issue(
                        {"key": "RELEASE-123"}, "development", "staging"
                    )

                    self.mock_logger.info.assert_called_with(
                        "Skipping RELEASE-123 label change since development label not found. "
                        "A comment will be added instead."
                    )
                    mock_add_comment.assert_called_once_with(
                        "RELEASE-123", "development", "staging", None
                    )

    @patch.dict(os.environ, {"JIRA_TOKEN": "test-token"})
    def test_process_release_ticket_success(self):
        """Test __process_release_issue method processes ticket successfully"""
        with patch("jira_ci.requests.Session"):
            client = jira_ci.JiraClient(self.mock_logger, self.mock_args)

            issue_data = {
                "fields": {
                    "status": {"name": "Release Pending"},
                    "labels": ["development"],
                }
            }

            with patch.object(
                client, "_JiraClient__get_ticket_data", return_value=issue_data
            ):
                with patch.object(
                    client, "_JiraClient__apply_label_change"
                ) as mock_apply_label:
                    with patch.object(
                        client, "_JiraClient__add_comment"
                    ) as mock_add_comment:
                        client._JiraClient__process_release_issue(
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
    def test_process_non_release_ticket_closed(self):
        """Test __process_non_release_issue method skips closed tickets"""
        with patch("jira_ci.requests.Session"):
            client = jira_ci.JiraClient(self.mock_logger, self.mock_args)

            issue_data = {"fields": {"status": {"name": "Closed"}}}

            with patch.object(
                client, "_JiraClient__get_ticket_data", return_value=issue_data
            ):
                client._JiraClient__process_non_release_issue(
                    {"key": "OTHER-123"}, "development", "staging"
                )

                self.mock_logger.info.assert_called_with(
                    "Skipping OTHER-123 since it is closed"
                )

    @patch.dict(os.environ, {"JIRA_TOKEN": "test-token"})
    def test_process_non_release_ticket_success(self):
        """Test __process_non_release_issue method processes ticket successfully"""
        with patch("jira_ci.requests.Session"):
            client = jira_ci.JiraClient(self.mock_logger, self.mock_args)

            issue_data = {"fields": {"status": {"name": "Open"}}}

            with patch.object(
                client, "_JiraClient__get_ticket_data", return_value=issue_data
            ):
                with patch.object(
                    client, "_JiraClient__add_comment"
                ) as mock_add_comment:
                    client._JiraClient__process_non_release_issue(
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
        """Test process_tickets method with no tickets in metadata"""
        with patch("jira_ci.requests.Session"):
            client = jira_ci.JiraClient(self.mock_logger, self.mock_args)

            with patch.object(
                client, "_JiraClient__extract_keys", return_value=([], [])
            ):
                client.process_tickets([])

                self.mock_logger.info.assert_called_with(
                    "No tickets found in tickets metadata, skipping."
                )

    @patch.dict(os.environ, {"JIRA_TOKEN": "test-token"})
    def test_process_tickets_with_release_tickets(self):
        """Test process_tickets method with release and non-release tickets"""
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
                "_JiraClient__extract_keys",
                return_value=(release_tickets, non_release_tickets),
            ):
                with patch.object(
                    client, "_JiraClient__process_release_issue"
                ) as mock_process_release:
                    with patch.object(
                        client, "_JiraClient__process_non_release_issue"
                    ) as mock_process_non_release:
                        client.process_tickets([])

                        mock_process_release.assert_called_once_with(
                            release_tickets[0], "development", "staging"
                        )
                        mock_process_non_release.assert_called_once_with(
                            non_release_tickets[0], "development", "staging"
                        )

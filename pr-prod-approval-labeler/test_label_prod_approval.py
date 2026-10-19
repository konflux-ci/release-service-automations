"""Test approval-label reconciliation and GitHub CLI requests."""

from __future__ import annotations

import io
import json
import subprocess
import unittest
from unittest.mock import call, patch

import label_prod_approval as labeler


def review(
    number: int,
    state: str,
    reviewer: str = "writer",
    submitted_at: str = "2026-10-19T10:00:00Z",
) -> dict:
    return {
        "id": number,
        "state": state,
        "user": {"id": reviewer, "login": reviewer},
        "submitted_at": submitted_at,
        "commit_id": "older-than-pr-head",
    }


class ReviewLabelTests(unittest.TestCase):
    def test_messages_use_stderr(self) -> None:
        with patch("sys.stderr", new_callable=io.StringIO) as stderr, patch(
            "sys.stdout", new_callable=io.StringIO
        ) as stdout:
            labeler._info("Label updated")
            labeler._warn("API request failed")
            self.assertEqual(stderr.getvalue(), "Label updated\nWarning: API request failed\n")
            self.assertEqual(stdout.getvalue(), "")

    def test_latest_decisive_review(self) -> None:
        for state, expected in (
            ("COMMENTED", ["writer"]),
            ("PENDING", ["writer"]),
            ("DISMISSED", []),
            ("CHANGES_REQUESTED", []),
        ):
            with self.subTest(state=state):
                self.assertEqual(
                    labeler.current_approvers([review(1, "APPROVED"), review(2, state)]),
                    expected,
                )

    def test_submission_order_overrides_creation_order(self) -> None:
        self.assertEqual(
            labeler.current_approvers(
                [
                    review(2, "APPROVED"),
                    review(1, "CHANGES_REQUESTED", submitted_at="2026-10-19T11:00:00Z"),
                ]
            ),
            [],
        )

    def test_deleted_reviewer_is_ignored(self) -> None:
        self.assertEqual(labeler.current_approvers([{"user": None, "state": "APPROVED"}]), [])

    def test_reconciliation(self) -> None:
        approval = review(1, "APPROVED")
        cases = [
            ("writer", [[approval]], False, "write", "POST"),
            ("admin", [[approval]], False, "admin", "POST"),
            ("reader", [[approval]], False, "read", None),
            ("triage", [[approval]], True, "read", "DELETE"),
            ("retained approval", [[approval]], True, "write", None),
            ("no reviews", [[]], True, "write", "DELETE"),
            ("dismissal", [[review(1, "DISMISSED")]], True, "write", "DELETE"),
            (
                "paginated dismissal", [[approval], [review(2, "DISMISSED")]],
                True, "write", "DELETE",
            ),
            (
                "another approval remains", [[approval, review(2, "DISMISSED", "other")]],
                True, "write", None,
            ),
            (
                "paginated comment", [[approval], [review(2, "COMMENTED")]],
                False, "write", "POST",
            ),
        ]
        for name, pages, has_label, permission, method in cases:
            with self.subTest(name=name), patch.object(labeler, "github_api") as api:
                def response(endpoint: str, **options: object) -> dict | list | None:
                    if options.get("method"):
                        return None
                    if "/reviews?" in endpoint:
                        return pages
                    if endpoint.endswith("/permission"):
                        return {"permission": permission}
                    return {
                        "state": "open",
                        "labels": [{"name": labeler.LABEL}] if has_label else [],
                    }

                api.side_effect = response
                labeler.synchronize_label("example/repo", "42")
                api.assert_any_call("repos/example/repo/pulls/42/reviews?per_page=100",
                                    paginate=True)
                mutations = [entry for entry in api.call_args_list if "method" in entry.kwargs]
                expected = []
                if method == "POST":
                    expected = [call("repos/example/repo/issues/42/labels", method="POST",
                                     payload={"labels": [labeler.LABEL]})]
                elif method == "DELETE":
                    expected = [call(
                        "repos/example/repo/issues/42/labels/prod%2Fneeds-approval",
                        method="DELETE",
                    )]
                self.assertEqual(mutations, expected)

    def test_closed_pr(self) -> None:
        with patch.object(labeler, "github_api", return_value={"state": "closed"}) as api:
            labeler.synchronize_label("example/repo", "42")
            api.assert_called_once_with("repos/example/repo/pulls/42")

    def test_api_errors_do_not_mutate_labels(self) -> None:
        environment = {"GH_TOKEN": "test-token", "GH_REPO": "example/repo", "PR_NUMBER": "42"}
        for failed_request in ("reviews", "permission"):
            responses = [{"state": "open", "labels": [{"name": labeler.LABEL}]}]
            if failed_request == "permission":
                responses.append([[review(1, "APPROVED")]])
            responses.append(subprocess.CalledProcessError(1, ["gh", "api"]))
            with self.subTest(request=failed_request), patch.dict(
                "os.environ", environment, clear=True
            ), patch.object(labeler, "github_api", side_effect=responses) as api:
                self.assertEqual(labeler.main(), 1)
                self.assertFalse(any("method" in entry.kwargs for entry in api.call_args_list))

    def test_required_environment_and_pr_number(self) -> None:
        environment = {"GH_TOKEN": "test-token", "GH_REPO": "example/repo", "PR_NUMBER": "42"}
        invalid_environments = [dict(environment, **{name: ""}) for name in environment]
        invalid_environments += [
            dict(environment, PR_NUMBER=value) for value in ("0", "x", "-1")
        ]
        invalid_environments.append({})
        for invalid_environment in invalid_environments:
            with self.subTest(environment=invalid_environment), patch.dict(
                "os.environ", invalid_environment, clear=True
            ), patch.object(labeler, "github_api") as api:
                self.assertEqual(labeler.main(), 1)
                api.assert_not_called()

    @patch.object(labeler.subprocess, "run")
    def test_cli_pagination(self, run: object) -> None:
        run.return_value.stdout = '[[{"id": 1}], [{"id": 2}]]'
        self.assertEqual(labeler.github_api("repos/example/repo/reviews", paginate=True),
                         [[{"id": 1}], [{"id": 2}]])
        run.assert_called_once_with(
            ["gh", "api", "repos/example/repo/reviews", "--method", "GET",
             "--paginate", "--slurp"],
            input=None, capture_output=True, text=True, check=True,
        )

    @patch.object(labeler.subprocess, "run")
    def test_cli_json_payload_and_empty_response(self, run: object) -> None:
        run.return_value.stdout = ""
        payload = {"labels": [labeler.LABEL]}
        self.assertIsNone(labeler.github_api("repos/example/repo/labels", "POST", payload))
        self.assertEqual(run.call_args.kwargs["input"], json.dumps(payload))
        self.assertEqual(run.call_args.args[0][-2:], ["--input", "-"])


if __name__ == "__main__":
    unittest.main()

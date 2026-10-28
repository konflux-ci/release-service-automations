"""Test approval-label reconciliation and GitHub CLI requests."""

from __future__ import annotations

import base64
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
    def test_pattern_matching(self) -> None:
        cases = [
            ("/src/", "src/nested/main.py", True),
            ("/src/", "other/src/main.py", False),
            ("apps/", "nested/apps/main.py", True),
            ("docs/*", "docs/main.md", True),
            ("docs/*", "docs/nested/main.md", False),
            ("*.py", "nested/main.py", True),
            ("/config.yaml", "nested/config.yaml", False),
            ("**/config.yaml", "config.yaml", True),
            ("**/config.yaml", "nested/deeper/config.yaml", True),
            ("a/**/b", "a/b", True),
            ("a/**/b", "a/x/y/b", True),
            ("a/**/b", "nested/a/b", False),
            ("a/**", "a/x/y/b", True),
            ("a**b", "axb", True),
            ("a**b", "a/x/b", False),
            ("/file?.py", "file1.py", True),
            ("/file?.py", "file12.py", False),
            ("/a+b.txt", "a+b.txt", True),
            ("/a+b.txt", "abxtxt", False),
            ("/apps/github", "apps/github/main.py", True),
            ("/apps/github", "apps/github-other/main.py", False),
        ]
        for pattern, path, expected in cases:
            with self.subTest(pattern=pattern, path=path):
                self.assertEqual(
                    bool(labeler.compile_codeowners_pattern(pattern).search(path)), expected
                )

    def test_root_codeowners_matching(self) -> None:
        contents = {
            "type": "file", "encoding": "base64",
            "content": base64.b64encode(b"""# Production paths
/src/ @writer
docs/* @writer
**/config?.yaml @writer
/src/examples/
/src/examples/owned.py @writer
/apps/ @writer
/apps/github
/hash/a#b @writer
/space\\ dir/ @writer
![invalid @writer
*.md # @not-an-owner
""").decode(),
        }
        with patch.object(labeler, "github_api", return_value=contents) as api:
            owners = labeler.load_codeowners("repos/example/repo", "base-sha")
            api.assert_called_once_with(
                "repos/example/repo/contents/CODEOWNERS?ref=base-sha",
                allow_not_found=True,
            )
        for path, expected in [
            ("src/main.py", True), ("src/nested/main.py", True),
            ("other/src/main.py", False), ("src2/main.py", False),
            ("SRC/main.py", False), ("src/examples/demo.py", False),
            ("src/examples/owned.py", True),
            ("docs/readme.txt", True), ("docs/nested/readme.txt", False),
            ("config1.yaml", True), ("nested/config2.yaml", True),
            ("config12.yaml", False), ("apps/service/main.py", True),
            ("apps/github/main.py", False), ("space dir/main.py", True),
            ("README.md", False),
            ("hash/a#b", True), ("hash/a", False),
        ]:
            with self.subTest(path=path):
                self.assertEqual(labeler.has_codeowner(path, owners), expected)

    def test_missing_root_codeowners_uses_existing_label_behavior(self) -> None:
        for state, labels, expected in (
            ("APPROVED", [], "POST"),
            ("DISMISSED", [{"name": labeler.LABEL}], "DELETE"),
        ):
            pull_request = {"state": "open", "base": {"sha": "trusted-base"},
                            "labels": labels}
            responses = [pull_request, None, [[review(1, state)]]]
            if state == "APPROVED":
                responses.append({"permission": "write"})
            responses.append(None)
            with self.subTest(state=state), patch.object(
                labeler, "github_api", side_effect=responses
            ) as api:
                labeler.synchronize_label("example/repo", "42")
                self.assertEqual(api.call_args.kwargs["method"], expected)
                contents_calls = [c for c in api.call_args_list if "/contents/" in c.args[0]]
                self.assertEqual(contents_calls, [call(
                    "repos/example/repo/contents/CODEOWNERS?ref=trusted-base",
                    allow_not_found=True,
                )])
                self.assertFalse(any("/files?" in c.args[0] for c in api.call_args_list))

    def test_changed_files_gate(self) -> None:
        cases = [
            ([{"filename": "README.md"}], False),
            ([{"filename": "src/main.py", "status": "modified"}], True),
            ([{"filename": "src/old.py", "status": "removed"}], True),
            ([{"filename": "docs/moved.py", "previous_filename": "src/main.py"}], True),
            ([{"filename": "src/main.py", "previous_filename": "docs/moved.py"}], True),
            ([{"filename": "README.md"}, {"filename": "src/main.py"}], True),
        ]
        for files, expected in cases:
            pull_request = {"base": {"sha": "trusted-base"}, "changed_files": len(files)}
            contents = {"type": "file", "encoding": "base64",
                        "content": base64.b64encode(b"/src/ @writer").decode()}
            with self.subTest(files=files), patch.object(
                labeler, "github_api", side_effect=[contents, [[file] for file in files]]
            ) as api:
                self.assertEqual(labeler.requires_prod_approval(
                    "repos/example/repo", "42", pull_request
                ), expected)
                api.assert_has_calls([
                    call("repos/example/repo/contents/CODEOWNERS?ref=trusted-base",
                         allow_not_found=True),
                    call("repos/example/repo/pulls/42/files?per_page=100", paginate=True),
                ])

    def test_nonmatching_pr_leaves_labels_and_reviews_untouched(self) -> None:
        for labels in ([], [{"name": labeler.LABEL}], [{"name": labeler.APPROVED_LABEL}],
                       [{"name": labeler.LABEL}, {"name": labeler.APPROVED_LABEL}]):
            pull_request = {"state": "open", "base": {"sha": "base-sha"},
                            "changed_files": 1, "labels": labels}
            contents = {"type": "file", "encoding": "base64",
                        "content": base64.b64encode(b"/src/ @writer").decode()}
            empty = {"type": "file", "encoding": "base64", "content": ""}
            for responses in ([empty, [[{"filename": "README.md"}]]],
                              [contents, [[{"filename": "README.md"}]]]):
                with self.subTest(labels=labels, responses=responses), patch.object(
                    labeler, "github_api", side_effect=[pull_request, *responses]
                ) as api:
                    labeler.synchronize_label("example/repo", "42")
                    self.assertFalse(any("/reviews?" in c.args[0] for c in api.call_args_list))
                    self.assertFalse(any("method" in c.kwargs for c in api.call_args_list))

    def test_incomplete_file_listing_fails(self) -> None:
        owners = [(labeler.compile_codeowners_pattern("*"), True)]
        pull_request = {"base": {"sha": "base"}, "changed_files": 3001}
        with patch.object(labeler, "load_codeowners", return_value=owners), \
                patch.object(labeler, "github_api", return_value=[[{"filename": "src/a"}]]):
            with self.assertRaisesRegex(ValueError, "incomplete"):
                labeler.requires_prod_approval(
                    "repos/example/repo", "42", pull_request
                )

    def test_only_explicit_404_is_treated_as_absent(self) -> None:
        for status in ("404", "403", "500", "not-json"):
            error = subprocess.CalledProcessError(
                1, ["gh", "api"], output=json.dumps({"status": status})
            )
            with self.subTest(status=status), patch.object(
                labeler.subprocess, "run", side_effect=error
            ):
                if status == "404":
                    self.assertIsNone(labeler.github_api("contents", allow_not_found=True))
                else:
                    with self.assertRaises(subprocess.CalledProcessError):
                        labeler.github_api("contents", allow_not_found=True)

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

    @patch.object(labeler, "requires_prod_approval", return_value=True)
    def test_reconciliation(self, gate: object) -> None:
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

    @patch.object(labeler, "requires_prod_approval", return_value=True)
    def test_prod_labels_after_dismissal(self, gate: object) -> None:
        for has_other_approval in (False, True):
            for labels in ([], [labeler.APPROVED_LABEL],
                           [labeler.LABEL, labeler.APPROVED_LABEL]):
                existing_labels = [*labels, "unrelated"]
                reviews = [review(1, "DISMISSED")]
                if has_other_approval:
                    reviews.append(review(2, "APPROVED", "other"))

                def response(endpoint: str, **options: object) -> dict | list | None:
                    if options.get("method"):
                        return None
                    if "/reviews?" in endpoint:
                        return [reviews]
                    if endpoint.endswith("/permission"):
                        return {"permission": "write"}
                    return {"state": "open", "labels": [
                        {"name": label} for label in existing_labels
                    ]}

                with self.subTest(labels=labels, other_approval=has_other_approval), \
                        patch.object(labeler, "github_api", side_effect=response) as api:
                    labeler.synchronize_label("example/repo", "42")
                    mutations = [c for c in api.call_args_list if "method" in c.kwargs]
                    if has_other_approval:
                        expected = [] if labeler.LABEL in labels else [call(
                            "repos/example/repo/issues/42/labels", method="POST",
                            payload={"labels": [labeler.LABEL]},
                        )]
                    else:
                        expected = [call(
                            f"repos/example/repo/issues/42/labels/{label.replace('/', '%2F')}",
                            method="DELETE",
                        ) for label in labels]
                    self.assertEqual(mutations, expected)

    def test_closed_pr(self) -> None:
        with patch.object(labeler, "github_api", return_value={"state": "closed"}) as api:
            labeler.synchronize_label("example/repo", "42")
            api.assert_called_once_with("repos/example/repo/pulls/42")

    @patch.object(labeler, "requires_prod_approval", return_value=True)
    def test_api_errors_do_not_mutate_labels(self, gate: object) -> None:
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

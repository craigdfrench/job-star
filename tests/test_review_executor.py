"""Regression test for the review gate's repo normalization.

The review executor used to pass `metadata.repo` (the owner/name slug the
deploy gate identifies a repo by) straight to `git clone`/`git ls-remote`,
which reject it with "repository 'owner/name' does not exist". PR #7 fixed
this in the CI executor by adding `_to_git_url()`; this test pins the same
fix for the review executor: a slug is normalized to a GitHub HTTPS URL
before reaching git.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from job_star.executors.review import ReviewExecutor


def test_prepare_target_worktree_normalizes_slug_to_url(monkeypatch, tmp_path):
    """A slug passed to _prepare_target_worktree must reach `git clone` as a URL."""
    ex = ReviewExecutor()

    clone_args: list[list[str]] = []

    def fake_git(args, cwd):
        # Record every git invocation. Make clone succeed with an empty dir
        # and ls-remote return a plausible SHA so the checkout path runs.
        clone_args.append(args)
        class R:
            returncode = 0
            stdout = ""
            stderr = ""
        if args and args[0] == "clone":
            # git clone --no-checkout <repo> <dir>
            target = Path(args[-1])
            target.mkdir(parents=True, exist_ok=True)
            (target / ".git").mkdir(exist_ok=True)
        if args and args[0] == "ls-remote":
            R.stdout = "57dc9aabaab603770f8d33cf4e335b6b4822ab3d\trefs/pull/92/head\n"
        return R()

    monkeypatch.setattr(ex, "_git", fake_git)

    repo_slug = "craigdfrench/gatehouse-ai"
    work_dir = str(tmp_path / "wt")
    ok, err = ex._prepare_target_worktree(repo_slug, "pull/92/head", work_dir)

    assert ok, f"expected success, got error: {err}"
    # The clone invocation must have used a full URL, not the bare slug.
    clone_invocations = [a for a in clone_args if a and a[0] == "clone"]
    assert clone_invocations, "no clone invocation captured"
    repo_arg = clone_invocations[0][2]  # ['clone', '--no-checkout', <repo>, <dir>]
    assert repo_arg == "https://github.com/craigdfrench/gatehouse-ai", (
        f"clone used {repo_arg!r} — slug was not normalized to a URL"
    )
    # ls-remote must also have received a URL.
    lsremote_invocations = [a for a in clone_args if a and a[0] == "ls-remote"]
    assert lsremote_invocations, "no ls-remote invocation captured"
    ls_repo_arg = lsremote_invocations[0][1]  # ['ls-remote', <repo>, <ref>]
    assert ls_repo_arg == "https://github.com/craigdfrench/gatehouse-ai", (
        f"ls-remote used {ls_repo_arg!r} — slug was not normalized to a URL"
    )


def test_to_git_url_shared_with_ci_executor():
    """Review imports the same _to_git_url the CI executor exposes."""
    from job_star.executors import ci as ci_mod
    from job_star.executors import review as review_mod
    assert review_mod._to_git_url is ci_mod._to_git_url

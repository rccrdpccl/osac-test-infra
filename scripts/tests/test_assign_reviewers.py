from __future__ import annotations

from datetime import datetime, timedelta, timezone

from scripts.assign_reviewers import (
    filter_vendored,
    is_vendored,
    match_codeowners_file,
    parse_codeowners,
    pick_from_codeowners,
    rank_candidates,
    recency_weight,
    score_commits,
)


class TestIsVendored:
    def test_vendor_directory(self):
        assert is_vendored("vendor/github.com/foo/bar.go")

    def test_nested_vendor(self):
        assert is_vendored("pkg/vendor/foo.go")

    def test_go_sum(self):
        assert is_vendored("go.sum")

    def test_go_mod(self):
        assert is_vendored("go.mod")

    def test_protobuf_generated(self):
        assert is_vendored("api/v1/types.pb.go")
        assert is_vendored("api/v1/types.pb.gw.go")

    def test_deepcopy_generated(self):
        assert is_vendored("api/v1/zz_generated.deepcopy.go")

    def test_codegen_generated(self):
        assert is_vendored("internal/foo_generated.go")

    def test_openapi(self):
        assert is_vendored("openapi/v2/openapi.json")
        assert is_vendored("docs/openapi/spec.yaml")

    def test_lock_files(self):
        assert is_vendored("package-lock.json")
        assert is_vendored("yarn.lock")
        assert is_vendored("pnpm-lock.yaml")
        assert is_vendored("uv.lock")
        assert is_vendored(".terraform.lock.hcl")

    def test_normal_go_file(self):
        assert not is_vendored("internal/server.go")

    def test_normal_python_file(self):
        assert not is_vendored("scripts/assign_reviewers.py")

    def test_normal_tf_file(self):
        assert not is_vendored("main.tf")

    def test_readme(self):
        assert not is_vendored("README.md")


class TestFilterVendored:
    def test_mixed_files(self):
        files = ["main.go", "vendor/foo.go", "go.sum", "internal/server.go"]
        kept, skipped = filter_vendored(files)
        assert kept == ["main.go", "internal/server.go"]
        assert skipped == 2

    def test_all_vendored(self):
        files = ["vendor/foo.go", "go.sum", "go.mod"]
        kept, skipped = filter_vendored(files)
        assert kept == []
        assert skipped == 3

    def test_none_vendored(self):
        files = ["main.go", "server.go"]
        kept, skipped = filter_vendored(files)
        assert kept == ["main.go", "server.go"]
        assert skipped == 0

    def test_empty(self):
        kept, skipped = filter_vendored([])
        assert kept == []
        assert skipped == 0


class TestRecencyWeight:
    def setup_method(self):
        self.now = datetime(2026, 7, 15, tzinfo=timezone.utc)

    def test_recent_commit(self):
        date = (self.now - timedelta(days=5)).isoformat()
        assert recency_weight(date, self.now) == 3

    def test_one_month_boundary(self):
        date = (self.now - timedelta(days=30)).isoformat()
        assert recency_weight(date, self.now) == 3

    def test_two_months(self):
        date = (self.now - timedelta(days=60)).isoformat()
        assert recency_weight(date, self.now) == 2

    def test_five_months(self):
        date = (self.now - timedelta(days=150)).isoformat()
        assert recency_weight(date, self.now) == 1

    def test_old_commit(self):
        date = (self.now - timedelta(days=200)).isoformat()
        assert recency_weight(date, self.now) == 0

    def test_invalid_date(self):
        assert recency_weight("not-a-date", self.now) == 0

    def test_z_suffix(self):
        date = "2026-07-10T12:00:00Z"
        assert recency_weight(date, self.now) == 3


class TestScoreCommits:
    def setup_method(self):
        self.now = datetime(2026, 7, 15, tzinfo=timezone.utc)

    def _commit(self, login, days_ago):
        date = (self.now - timedelta(days=days_ago)).isoformat()
        return {
            "author": {"login": login},
            "commit": {"author": {"date": date}},
        }

    def test_basic_scoring(self):
        commits = [
            self._commit("alice", 5),
            self._commit("bob", 60),
            self._commit("alice", 10),
        ]
        scores = score_commits(commits, "pr-author", self.now)
        assert scores == {"alice": 6, "bob": 2}

    def test_excludes_pr_author(self):
        commits = [self._commit("alice", 5)]
        scores = score_commits(commits, "alice", self.now)
        assert scores == {}

    def test_excludes_bots(self):
        commits = [
            self._commit("dependabot[bot]", 5),
            self._commit("openshift-merge-robot", 10),
            self._commit("openshift-merge-bot[bot]", 15),
        ]
        scores = score_commits(commits, "human", self.now)
        assert scores == {}

    def test_null_author(self):
        commits = [{"author": None, "commit": {"author": {"date": "2026-07-10T00:00:00Z"}}}]
        scores = score_commits(commits, "pr-author", self.now)
        assert scores == {}

    def test_empty_commits(self):
        scores = score_commits([], "pr-author", self.now)
        assert scores == {}

    def test_old_commits_ignored(self):
        commits = [self._commit("alice", 200)]
        scores = score_commits(commits, "pr-author", self.now)
        assert scores == {}


class TestParseCodeowners:
    def test_basic(self):
        content = "* @alice @bob\n/docs/ @carol\n"
        rules = parse_codeowners(content)
        assert rules == [("*", ["alice", "bob"]), ("/docs/", ["carol"])]

    def test_comments_and_blanks(self):
        content = "# Header comment\n\n* @alice  # inline comment\n\n"
        rules = parse_codeowners(content)
        assert rules == [("*", ["alice"])]

    def test_empty(self):
        assert parse_codeowners("") == []

    def test_pattern_without_owners(self):
        content = "*.md\n*.go @alice\n"
        rules = parse_codeowners(content)
        assert rules == [("*.go", ["alice"])]


class TestMatchCodeownersFile:
    def test_wildcard(self):
        assert match_codeowners_file("*", "any/file.go")

    def test_extension(self):
        assert match_codeowners_file("*.go", "internal/server.go")
        assert not match_codeowners_file("*.go", "main.py")

    def test_directory_pattern(self):
        assert match_codeowners_file("/docs/", "docs/README.md")
        assert match_codeowners_file("docs/", "docs/guide.md")
        assert not match_codeowners_file("/docs/", "src/docs/file.md")

    def test_specific_file(self):
        assert match_codeowners_file("Makefile", "Makefile")

    def test_path_pattern(self):
        assert match_codeowners_file("internal/servers/", "internal/servers/grpc.go")
        assert not match_codeowners_file("internal/servers/", "internal/clients/http.go")


class TestPickFromCodeowners:
    CODEOWNERS = """\
* @alice @bob
/docs/ @carol
internal/servers/ @dave @eve
*.proto @frank
"""

    def test_matches_specific_pattern(self):
        files = ["internal/servers/grpc.go"]
        result = pick_from_codeowners(self.CODEOWNERS, files, "pr-author", 1)
        assert result == ["dave"] or result == ["eve"]
        assert len(result) == 1

    def test_matches_multiple_files(self):
        files = ["internal/servers/grpc.go", "internal/servers/rest.go", "README.md"]
        result = pick_from_codeowners(self.CODEOWNERS, files, "pr-author", 2)
        assert len(result) == 2
        assert "dave" in result or "eve" in result

    def test_excludes_pr_author(self):
        files = ["docs/README.md"]
        result = pick_from_codeowners(self.CODEOWNERS, files, "carol", 1)
        assert "carol" not in result

    def test_falls_back_to_wildcard(self):
        files = ["some/random/file.txt"]
        result = pick_from_codeowners(self.CODEOWNERS, files, "pr-author", 1)
        assert result[0] in ("alice", "bob")

    def test_empty_codeowners(self):
        result = pick_from_codeowners("", ["file.go"], "pr-author", 1)
        assert result == []

    def test_proto_pattern(self):
        files = ["api/v1/types.proto"]
        result = pick_from_codeowners(self.CODEOWNERS, files, "pr-author", 1)
        assert result == ["frank"]


class TestRankCandidates:
    def test_ranks_by_score(self):
        scores = {"alice": 10, "bob": 5, "carol": 8}
        assert rank_candidates(scores, 2) == ["alice", "carol"]

    def test_respects_count(self):
        scores = {"alice": 10, "bob": 5}
        assert rank_candidates(scores, 1) == ["alice"]

    def test_empty(self):
        assert rank_candidates({}, 2) == []

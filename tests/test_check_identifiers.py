import hashlib
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("check_identifiers", ROOT / "scripts" / "check_identifiers.py")
ci = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ci)


def h(token):
    return hashlib.sha256(token.encode()).hexdigest()


def test_blocked_word_found_case_insensitively():
    assert ci.violations("Hello ZZForbiddenZZ world", blocked={h("zzforbiddenzz")})


def test_blocked_word_inside_hyphenated_domain():
    assert ci.violations("see zzforbiddenzz-consulting.test", blocked={h("zzforbiddenzz")})


def test_phone_number_with_separators_matches_digits():
    assert ci.violations("call +32 2 555 01 99 now", blocked={h("3225550199")})


def test_uuid_token():
    u = "11111111-2222-4333-8444-555555555555"
    assert ci.violations(u, blocked={h(u)})


def test_violation_text_never_contains_the_token():
    out = ci.violations("zzforbiddenzz", blocked={h("zzforbiddenzz")})
    assert out and "zzforbiddenzz" not in " ".join(out)


def test_clean_text_passes():
    text = "info@acme.example 46.19.208.0/21 185.238.172.0/22 203.0.113.10 127.0.0.1 10.0.0.0/24"
    assert ci.violations(text, blocked={h("zz")}) == []


def test_public_ip_outside_allowed_ranges_fails():
    ip = ".".join(["93", "184", "216", "34"])
    assert any("public IPv4" in v for v in ci.violations(f"bind = {ip}", blocked=set()))


def test_version_strings_are_not_ips():
    assert ci.violations("pkg==25.1.0.1 v1.2.3.4", blocked=set()) == []


def test_email_outside_example_domains_fails():
    addr = "someone" + "@" + "company" + ".test"
    assert any("e-mail" in v for v in ci.violations(addr, blocked=set()))


def test_example_and_attribution_emails_pass():
    text = ("a@acme.example b@example.com c@example.org noreply@anthropic.com git@github.com "
            "sip:3220000001@127.0.0.1 aivm-spool@192.0.2.20")
    assert ci.violations(text, blocked=set()) == []


def test_host_alias_patterns():
    assert ci.violations("run " + "ssh " + "nas" + " ls", blocked=set())
    assert ci.violations("open " + "https://" + "home" + "/page", blocked=set())


def test_blocklist_loaded_from_env_and_file_never_from_repo(tmp_path):
    assert ci.load_blocklist(tmp_path, env={}) == frozenset()
    got = ci.load_blocklist(tmp_path, env={"AIVM_BLOCKLIST": "ZZOne, zztwo\nzzthree"})
    assert got == {h("zzone"), h("zztwo"), h("zzthree")}
    (tmp_path / ".identifier-blocklist").write_text("# comment\nZZFour\n")
    assert ci.load_blocklist(tmp_path, env={}) == {h("zzfour")}
    source = (ROOT / "scripts" / "check_identifiers.py").read_text()
    assert not __import__("re").search(r"[0-9a-f]{64}", source)


def test_gitignore_excludes_blocklist_file():
    assert ".identifier-blocklist" in (ROOT / ".gitignore").read_text().split()


def test_repository_worktree_is_clean():
    assert ci.scan_worktree(ROOT) == []


def _git(repo, *args, env=None):
    import os
    import subprocess
    full_env = {**os.environ, **(env or {})}
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, env=full_env)


def _repo_with_commit(tmp_path, author_email, committer_email, tag_message=None):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "f.txt").write_text("clean\n")
    _git(repo, "add", "f.txt")
    env = {"GIT_AUTHOR_NAME": "a", "GIT_AUTHOR_EMAIL": author_email,
           "GIT_COMMITTER_NAME": "c", "GIT_COMMITTER_EMAIL": committer_email}
    _git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "clean", env=env)
    if tag_message:
        _git(repo, "-c", "tag.gpgsign=false", "tag", "-a", "v0", "-m", tag_message, env=env)
    return repo


def test_history_scan_flags_blocked_author_email_domain(tmp_path):
    repo = _repo_with_commit(tmp_path, "someone" + "@" + "zzforbiddenzz.test", "41694587+x@users.noreply.github.com")
    found = ci.scan_history(repo, blocked={h("zzforbiddenzz")})
    assert any(v.startswith("commit metadata:") for v in found)
    assert "zzforbiddenzz" not in " ".join(found)


def test_history_scan_flags_committer_email_outside_example_domains(tmp_path):
    repo = _repo_with_commit(tmp_path, "41694587+x@users.noreply.github.com", "someone" + "@" + "company.test")
    assert any("commit metadata" in v and "e-mail" in v for v in ci.scan_history(repo))


def test_history_scan_allows_noreply_identities(tmp_path):
    repo = _repo_with_commit(tmp_path, "41694587+x@users.noreply.github.com", "noreply@anthropic.com")
    assert ci.scan_history(repo, blocked={h("zzforbiddenzz")}) == []


def test_history_scan_checks_tag_contents(tmp_path):
    repo = _repo_with_commit(tmp_path, "41694587+x@users.noreply.github.com",
                             "41694587+x@users.noreply.github.com", tag_message="release zzforbiddenzz")
    found = ci.scan_history(repo, blocked={h("zzforbiddenzz")})
    assert any(v.startswith("tags:") for v in found)

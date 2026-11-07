from pathlib import Path
import subprocess


def test_retry_git_push_helper_has_valid_bash_syntax():
    script = Path(__file__).parents[1] / "scripts" / "retry_git_push.sh"
    subprocess.run(["bash", "-n", str(script)], check=True)


def test_retry_git_push_helper_expands_shell_variables():
    script = Path(__file__).parents[1] / "scripts" / "retry_git_push.sh"
    content = script.read_text()
    assert 'branch="${1:-${GITHUB_REF_NAME:-main}}"' in content
    assert 'max_attempts="${MAX_GIT_PUSH_ATTEMPTS:-6}"' in content
    assert "\\${" not in content

from pathlib import Path
import subprocess


def test_retry_git_push_helper_has_valid_bash_syntax():
    script = Path(__file__).parents[1] / "scripts" / "retry_git_push.sh"
    subprocess.run(["bash", "-n", str(script)], check=True)

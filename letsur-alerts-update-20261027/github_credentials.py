"""Read GitHub credentials privately via the configured Git credential helper."""
import os
import subprocess


def github_token():
    token = os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
    if token:
        return token
    env = dict(os.environ, GIT_TERMINAL_PROMPT='0', GCM_INTERACTIVE='never')
    try:
        result = subprocess.run(['git', 'credential', 'fill'],
            input='protocol=https\nhost=github.com\n\n', text=True,
            capture_output=True, timeout=20, env=env)
        if result.returncode == 0:
            return dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line).get('password')
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


if __name__ == '__main__':
    print({'github_credentials_available': bool(github_token())})

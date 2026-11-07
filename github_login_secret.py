"""Optional, encrypted upload of QR credentials to the user's GitHub Actions."""
import base64
import json
import os
import re
import shutil
import subprocess
from urllib.parse import quote

import requests
from nacl.public import PublicKey, SealedBox

from login_store import SECRET_NAME, validate_login


def _github_token():
    token = os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN")
    if token:
        return token
    executable = shutil.which("git")
    if executable:
        try:
            result = subprocess.run(
                [executable, "credential", "fill"], input="protocol=https\nhost=github.com\n\n",
                text=True, capture_output=True, timeout=20,
                env=dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="Never"),
            )
            if result.returncode == 0:
                fields = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
                if fields.get("password"):
                    return fields["password"]
        except (OSError, subprocess.TimeoutExpired):
            pass
    raise RuntimeError("未找到 GitHub 登录凭据。登录已保存在本机；请配置 GH_TOKEN 或 Git 凭据后再同步。")


def upload_login(repository, login, environment=None, token=None, session=None):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) or any(part in {".", ".."} for part in repository.split("/")):
        raise RuntimeError("GitHub 仓库必须是 owner/repository 格式。")
    login = validate_login(login)
    token = token or _github_token()
    base = f"https://api.github.com/repos/{repository}"
    base += f"/environments/{quote(environment, safe='')}/secrets" if environment else "/actions/secrets"
    http = session or requests.Session()
    headers = {"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json"}
    try:
        response = http.get(base + "/public-key", headers=headers, timeout=(10, 30), allow_redirects=False)
        if response.status_code != 200:
            raise RuntimeError(f"无法取得 GitHub Secret 公钥，HTTP {response.status_code}；本地登录已保留。")
        public = response.json()
        box = SealedBox(PublicKey(base64.b64decode(public["key"])))
        encrypted = base64.b64encode(box.encrypt(json.dumps(login, ensure_ascii=False).encode())).decode()
        response = http.put(
            base + "/" + SECRET_NAME, headers=headers,
            json={"key_id": public["key_id"], "encrypted_value": encrypted},
            timeout=(10, 30), allow_redirects=False,
        )
        if response.status_code not in (201, 204):
            raise RuntimeError(f"GitHub Secret 同步失败，HTTP {response.status_code}；本地登录已保留。")
    except requests.RequestException as exc:
        raise RuntimeError("GitHub 连接失败；本地登录已保留，稍后可重新同步。") from exc
    except (ValueError, KeyError, TypeError) as exc:
        raise RuntimeError("GitHub Secret 公钥响应无效；本地登录已保留。") from exc
    finally:
        if session is None:
            http.close()

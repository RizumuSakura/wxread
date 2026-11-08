"""Environment configuration; no embedded account or random reading templates."""
import os
import shlex

from login_store import load_login, login_path
from weread_auth import USER_AGENT


try:
    READ_NUM = int(os.getenv("READ_NUM") or 40)
except ValueError as exc:
    raise RuntimeError("READ_NUM 必须填写正整数，例如 40 或 120。") from exc
BOOK_URL = (os.getenv("WXREAD_BOOK_URL") or "").strip()
PUSH_METHOD = os.getenv("PUSH_METHOD")
PUSHPLUS_TOKEN = os.getenv("PUSHPLUS_TOKEN")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
WXPUSHER_SPT = os.getenv("WXPUSHER_SPT")
SERVERCHAN_SPT = os.getenv("SERVERCHAN_SPT")


def convert(curl_command):
    """Compatibility parser for previously configured cURL credentials."""
    tokens = shlex.split(curl_command.replace('\\\r\n', '').replace('\\\n', ''))
    parsed_headers = {}
    cookie_headers = []
    cookie_options = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        option, separator, value = token.partition('=')
        if token in ('-H', '--header', '-b', '--cookie'):
            index += 1
            if index >= len(tokens):
                raise ValueError('cURL 的 header/cookie 参数缺少值，请重新复制完整请求。')
            value = tokens[index]
            option = token
        elif not (separator and option in ('--header', '--cookie')):
            index += 1
            continue
        if option in ('-H', '--header'):
            name, colon, content = value.partition(':')
            if colon:
                name = name.strip().lower()
                if name == 'cookie':
                    cookie_headers.append(content.strip())
                elif name not in ('content-length', 'host'):
                    parsed_headers[name] = content.strip()
        else:
            cookie_options.append(value)
        index += 1
    parsed_cookies = {}
    for cookie_string in cookie_headers + cookie_options:
        for item in cookie_string.split(';'):
            name, separator, value = item.strip().partition('=')
            if separator and name:
                parsed_cookies[name] = value
    return parsed_headers, parsed_cookies


headers = {"accept": "application/json, text/plain, */*", "user-agent": USER_AGENT}
cookies = {}
cloud_exists = bool(os.getenv("WXREAD_QR_PASSWORD") and login_path().exists())
if cloud_exists or os.getenv("WXREAD_LOGIN_JSON"):
    login = load_login()
elif os.getenv("WXREAD_CURL_BASH"):
    headers, cookies = convert(os.environ["WXREAD_CURL_BASH"])
    login = None
else:
    login = load_login()
if login is not None:
    headers, cookies = login["headers"], login["cookies"]

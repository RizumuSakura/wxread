# main.py 主逻辑：包括字段拼接、模拟请求
import argparse
import hashlib
import logging
import random
import time
import urllib.parse

import requests

from config import data, headers, cookies, READ_NUM, PUSH_METHOD, book, chapter
from log_utils import setup_logging
from push import push
from weread_session import WeReadSession


KEY = "3c5c8717f3daf09iop3423zafeqoi"
READ_URL = "https://weread.qq.com/web/book/read"
FIX_SYNCKEY_URL = "https://weread.qq.com/web/book/chapterInfos"
SYNCKEY_REPAIR_LIMIT = 3
SYNCKEY_REPAIR_DELAY = 5
AUTH_ERROR_CODES = {"-2012", "-2013"}
AUTH_RETRY_LIMIT = 2
LOGIN_HELP = "请在微信读书网页版重新登录，并更新 GitHub Secret WXREAD_CURL_BASH（包含全部 Cookie 的 read 请求）。"


def encode_data(payload):
    """数据编码。"""
    return '&'.join(f"{key}={urllib.parse.quote(str(payload[key]), safe='')}" for key in sorted(payload))


def cal_hash(input_string):
    """计算哈希值。"""
    first = second = 0x15051505
    length = len(input_string)
    index = length - 1
    while index > 0:
        first = 0x7fffffff & (first ^ ord(input_string[index]) << (length - index) % 30)
        second = 0x7fffffff & (second ^ ord(input_string[index - 1]) << index % 30)
        index -= 2
    return hex(first + second)[2:].lower()


def refresh_cookie(client, strict=True):
    if client.renew():
        return True
    if strict:
        raise RuntimeError("登录会话无法续期；可能已失效或续期接口暂时不可用。" + LOGIN_HELP)
    logging.warning("启动续期未成功，使用当前 Cookie 尝试阅读。")
    return False


def fix_no_synckey(client):
    response = client.post(FIX_SYNCKEY_URL, {"bookIds": ["3300060341"]})
    response.raise_for_status()


def run_reading(client, refresh_print):
    if READ_NUM <= 0:
        raise RuntimeError("READ_NUM 必须大于 0。")
    refresh_cookie(client, strict=False)
    payload = data.copy()
    index = 1
    last_time = int(time.time()) - 30
    synckey_repair_attempts = 0
    auth_attempts = 0
    logging.info("一共需要阅读 %s 次。", READ_NUM)

    while index <= READ_NUM:
        payload.pop('s', None)
        payload['b'] = random.choice(book)
        payload['c'] = random.choice(chapter)
        this_time = int(time.time())
        payload['ct'] = this_time
        payload['rt'] = this_time - last_time
        payload['ts'] = this_time * 1000 + random.randint(0, 1000)
        payload['rn'] = random.randint(0, 1000)
        payload['sg'] = hashlib.sha256(f"{payload['ts']}{payload['rn']}{KEY}".encode()).hexdigest()
        payload['s'] = cal_hash(encode_data(payload))
        refresh_print(f"阅读进度: 第 {index}/{READ_NUM} 次，已完成 {(index - 1) * 0.5:.1f} 分钟")
        response = client.post(READ_URL, payload)
        try:
            result = response.json()
        except ValueError as exc:
            if response.status_code == 401:
                result = {}
            else:
                raise RuntimeError(f"read 接口返回非 JSON 响应，HTTP {response.status_code}。") from exc
        if not isinstance(result, dict):
            raise RuntimeError("read 接口返回了非预期的 JSON 结构。")

        if response.ok and result.get('succ') == 1:
            if 'synckey' in result:
                synckey_repair_attempts = auth_attempts = 0
                last_time = this_time
                index += 1
                time.sleep(30)
                refresh_print(f"阅读进度: 第 {index - 1}/{READ_NUM} 次，已完成 {(index - 1) * 0.5:.1f} 分钟")
            else:
                synckey_repair_attempts += 1
                if synckey_repair_attempts > SYNCKEY_REPAIR_LIMIT:
                    raise RuntimeError(f"连续 {SYNCKEY_REPAIR_LIMIT} 次未恢复 synckey，终止运行。")
                logging.warning("无 synckey，尝试修复（%s/%s）...", synckey_repair_attempts, SYNCKEY_REPAIR_LIMIT)
                fix_no_synckey(client)
                time.sleep(SYNCKEY_REPAIR_DELAY)
            continue

        err_code = result.get('errCode')
        if response.status_code == 401 or str(err_code) in AUTH_ERROR_CODES:
            auth_attempts += 1
            if auth_attempts > AUTH_RETRY_LIMIT:
                raise RuntimeError(f"连续 {AUTH_RETRY_LIMIT} 次续期后仍鉴权失败，停止重试。" + LOGIN_HELP)
            logging.warning("检测到鉴权异常，尝试续期（%s/%s）。", auth_attempts, AUTH_RETRY_LIMIT)
            refresh_cookie(client)
        else:
            # Business errors, rate limits and server failures are not login expiry.
            raise RuntimeError(f"read 接口异常：HTTP {response.status_code}，errCode={err_code}；停止本次任务。")
    return index - 1


def main(check_auth=False):
    refresh_print = setup_logging()
    client = None
    try:
        client = WeReadSession(headers, cookies)
        if check_auth:
            refresh_cookie(client)
            logging.info("登录续期检查通过，未执行阅读或消息推送。")
            return 0
        completed = run_reading(client, refresh_print)
        logging.info("阅读脚本已完成。")
        push(f"微信读书自动阅读完成。\n阅读时长：{completed * 0.5} 分钟。", PUSH_METHOD, is_success=True)
        return 0
    except (RuntimeError, requests.RequestException) as exc:
        logging.error("%s", exc)
        if not check_auth:
            push(str(exc), PUSH_METHOD, is_success=False)
        return 1
    finally:
        if client is not None:
            client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-auth", action="store_true", help="仅检查并保存登录续期，不执行阅读或推送")
    raise SystemExit(main(check_auth=parser.parse_args().check_auth))

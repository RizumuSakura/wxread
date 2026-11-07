"""Encoding used by the authenticated WeRead web reader."""
import hashlib
from urllib.parse import quote


JS_URI_SAFE = "~()*!'"


def encode_id(value):
    text = str(value)
    checksum = hashlib.md5(text.encode()).hexdigest()
    if text.isascii() and text.isdigit():
        chunks = [format(int(text[start:start + 9]), "x") for start in range(0, len(text), 9)]
        kind = "3"
    else:
        chunks, kind = ["".join(format(byte, "x") for byte in text.encode())], "4"
    encoded = checksum[:3] + kind + "2" + checksum[-2:]
    encoded += "g".join(format(len(chunk), "02x") + chunk for chunk in chunks)
    encoded += checksum[:max(0, 20 - len(encoded))]
    return encoded + hashlib.md5(encoded.encode()).hexdigest()[:3]


def encode_data(payload):
    def string(value):
        if isinstance(value, bool):
            return str(value).lower()
        return "null" if value is None else str(value)
    return "&".join(f"{key}={quote(string(payload[key]), safe=JS_URI_SAFE)}" for key in sorted(payload) if key != "s")


def cal_hash(text):
    first = second = 0x15051505
    index = len(text) - 1
    while index > 0:
        first = (first ^ ord(text[index]) << ((len(text) - index) % 30)) & 0x7fffffff
        second = (second ^ ord(text[index - 1]) << (index % 30)) & 0x7fffffff
        index -= 2
    return format(first + second, "x")


def app_id(user_agent):
    rolling = 0
    for byte in user_agent.encode():
        rolling = (131 * rolling + byte) & 0x7fffffff
    return "wb" + "".join(str(len(part) % 10) for part in user_agent.split()[:12]) + "h" + str(rolling)

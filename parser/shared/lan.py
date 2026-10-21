"""Detect LAN IPv4 addresses for sharing the web UI and ingest API."""
import os
import re
import socket
import subprocess
from typing import List


_IPV4_LINE_RE = re.compile(
    r"(?:IPv4[^\r\n:]{0,48}|IP Address[^\r\n:]{0,48}):\s*(\d{1,3}(?:\.\d{1,3}){3})",
    re.IGNORECASE,
)


def is_usable_ipv4(ip: str) -> bool:
    text = (ip or "").strip()
    parts = text.split(".")
    if len(parts) != 4:
        return False
    try:
        nums = [int(part) for part in parts]
    except ValueError:
        return False
    if any(n < 0 or n > 255 for n in nums):
        return False
    if nums[0] in (0, 127, 224, 225, 226, 227, 228, 229, 230, 231, 232, 233, 234, 235, 236, 237, 238, 239, 255):
        return False
    if nums[0] == 169 and nums[1] == 254:
        return False
    return True


def parse_ipv4_addresses(text: str) -> List[str]:
    found: List[str] = []
    for match in _IPV4_LINE_RE.findall(text or ""):
        if is_usable_ipv4(match) and match not in found:
            found.append(match)
    return found


def _add_ip(bucket: List[str], ip: str) -> None:
    if is_usable_ipv4(ip) and ip not in bucket:
        bucket.append(ip)


def _primary_outbound_ip() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    finally:
        sock.close()


def _hostname_ips() -> List[str]:
    found: List[str] = []
    hostname = socket.gethostname()
    try:
        _add_ip(found, socket.gethostbyname(hostname))
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            _add_ip(found, info[4][0])
    except OSError:
        pass
    return found


def _ipconfig_ips() -> List[str]:
    if os.name != "nt":
        return []
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        raw = subprocess.check_output(
            ["ipconfig"],
            stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
    except (OSError, subprocess.CalledProcessError):
        return []
    text = raw.decode("utf-8", errors="ignore")
    if "IPv4" not in text and "IP Address" not in text:
        text = raw.decode("gbk", errors="ignore")
    return parse_ipv4_addresses(text)


def list_lan_ipv4() -> List[str]:
    found: List[str] = []
    try:
        _add_ip(found, _primary_outbound_ip())
    except OSError:
        pass
    for ip in _hostname_ips():
        _add_ip(found, ip)
    for ip in _ipconfig_ips():
        _add_ip(found, ip)
    return found


def lan_urls(port: int, include_localhost: bool = True) -> List[str]:
    urls: List[str] = []
    if include_localhost:
        urls.append(f"http://127.0.0.1:{int(port)}")
    for ip in list_lan_ipv4():
        url = f"http://{ip}:{int(port)}"
        if url not in urls:
            urls.append(url)
    return urls

# -*- coding: utf-8 -*-
"""proxy_detect.py — 自动探测本机正在使用的 HTTP 代理。

背景
----
换机部署时，用户可能用 Clash / 自由猫 / v2rayN 等不同客户端，监听端口并不固定
（Clash Verge 常见 7897，自由猫常见 7892，v2rayN 常见 10809……）。原来各脚本把
"http://127.0.0.1:7897" 写死，部署到别的机器上就抓不到 TikTok。

探测顺序
--------
1. 环境变量 WB_PROXY（显式指定，最高优先；再退 HTTPS_PROXY / HTTP_PROXY / ALL_PROXY）
2. Windows 系统代理（注册表 Internet Settings 的 ProxyServer）
3. 本机常见代理端口 TCP 探测（只认端口能连上的）
4. 兜底 http://127.0.0.1:7897

用法
----
    from proxy_detect import detect_proxy
    PROXY = os.environ.get("WB_PROXY") or detect_proxy()

结果在进程内缓存；需要重新探测时调用 detect_proxy(force=True)。
"""
from __future__ import annotations

import os
import socket
from urllib.parse import urlsplit

# 常见客户端/端口，按本机常见程度排序：
#   7892 自由猫   7897 Clash Verge   7890 Clash   10809/10808 v2rayN
#   1080 Shadowsocks   8889 SSR   2080 v2rayA   33210/4780 其它客户端
CANDIDATE_PORTS = (
    7892, 7897, 7890, 10809, 10808, 1080, 8889, 2080, 33210, 4780, 8118,
)

_ENV_NAMES = (
    "WB_PROXY", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy",
    "ALL_PROXY", "all_proxy",
)

_cache: str | None = None


def _normalize(value: str | None) -> str:
    """把 '127.0.0.1:7892' / 'http://127.0.0.1:7892/' 统一成 'http://127.0.0.1:7892'。"""
    v = (value or "").strip()
    if not v:
        return ""
    if "://" not in v:
        v = "http://" + v
    return v.rstrip("/")


def _split_host_port(url: str) -> tuple[str, int]:
    parts = urlsplit(url if "://" in url else "http://" + url)
    return parts.hostname or "127.0.0.1", parts.port or 80


def _port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.25) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def _system_proxy() -> str:
    """读 Windows 系统代理设置；非 Windows 或未设置时返回空串。"""
    if os.name != "nt":
        return ""
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        ) as key:
            server, _ = winreg.QueryValueEx(key, "ProxyServer")
    except OSError:
        return ""
    server = str(server or "").strip()
    if not server:
        return ""
    # 形如 "http=127.0.0.1:7892;https=127.0.0.1:7892"
    if "=" in server:
        parts = {}
        for item in server.split(";"):
            if "=" in item:
                k, val = item.split("=", 1)
                parts[k.strip().lower()] = val.strip()
        server = parts.get("https") or parts.get("http") or next(iter(parts.values()), "")
    return _normalize(server)


def detect_proxy(force: bool = False) -> str:
    """返回本机可用的 HTTP 代理地址；探测不到时兜底 7897。"""
    global _cache
    if _cache and not force:
        return _cache

    for name in _ENV_NAMES:
        value = _normalize(os.environ.get(name))
        if value:
            _cache = value
            return value

    sys_proxy = _system_proxy()
    if sys_proxy:
        host, port = _split_host_port(sys_proxy)
        if _port_open(port, host):
            _cache = sys_proxy
            return sys_proxy

    for port in CANDIDATE_PORTS:
        if _port_open(port):
            _cache = f"http://127.0.0.1:{port}"
            return _cache

    _cache = "http://127.0.0.1:7897"
    return _cache


if __name__ == "__main__":
    print(detect_proxy(force=True))

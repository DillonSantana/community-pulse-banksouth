"""Polite HTTP client: per-host rate limiting, retries, robots.txt, TLS fallback."""
from __future__ import annotations

import logging
import threading
import time
import urllib.robotparser
from urllib.parse import urlsplit

import requests
import urllib3

log = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (compatible; CountyMeetingMonitor/0.1; public meeting records reader)"
MIN_HOST_INTERVAL = 1.0   # seconds between requests to the same host
TIMEOUT = 30


class FetchError(Exception):
    pass


class Client:
    def __init__(self, respect_robots: bool = True, min_interval: float = MIN_HOST_INTERVAL):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/json,application/xml;q=0.9,*/*;q=0.8",
        })
        self.respect_robots = respect_robots
        self.min_interval = min_interval
        self._last_hit: dict[str, float] = {}
        self._host_locks: dict[str, threading.Lock] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._insecure_hosts: set[str] = set()
        self._meta_lock = threading.Lock()

    # -- internals -------------------------------------------------------
    def _lock_for(self, host: str) -> threading.Lock:
        with self._meta_lock:
            return self._host_locks.setdefault(host, threading.Lock())

    def _interval_for(self, url: str) -> float:
        """Our default spacing, or the site's robots.txt Crawl-delay if longer (capped)."""
        parts = urlsplit(url)
        rp = self._robots.get(f"{parts.scheme}://{parts.netloc}")
        delay = None
        if rp is not None:
            try:
                delay = rp.crawl_delay(USER_AGENT)
            except Exception:
                delay = None
        return max(self.min_interval, min(float(delay or 0), 30.0))

    def _wait_turn(self, host: str, interval: float) -> None:
        last = self._last_hit.get(host, 0.0)
        gap = time.monotonic() - last
        if gap < interval:
            time.sleep(interval - gap)
        self._last_hit[host] = time.monotonic()

    def _allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        parts = urlsplit(url)
        root = f"{parts.scheme}://{parts.netloc}"
        if root not in self._robots:
            rp = urllib.robotparser.RobotFileParser()
            try:
                resp = self._raw_get(root + "/robots.txt", parts.netloc)
                if resp.status_code == 200 and "html" not in resp.headers.get("content-type", ""):
                    rp.parse(resp.text.splitlines())
                    self._robots[root] = rp
                else:
                    self._robots[root] = None   # no usable robots.txt -> allowed
            except Exception:
                self._robots[root] = None
        rp = self._robots[root]
        return True if rp is None else rp.can_fetch(USER_AGENT, url)

    def _raw_get(self, url: str, host: str, **kw) -> requests.Response:
        verify = host not in self._insecure_hosts
        try:
            return self.session.get(url, timeout=TIMEOUT, verify=verify, **kw)
        except requests.exceptions.SSLError:
            # A few small county sites ship broken certificate chains. These are
            # public, read-only pages, so retry without verification and log it.
            log.warning("TLS verification failed for %s; retrying unverified", host)
            self._insecure_hosts.add(host)
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            return self.session.get(url, timeout=TIMEOUT, verify=False, **kw)

    # -- public ----------------------------------------------------------
    def get(self, url: str, *, retries: int = 2, **kw) -> requests.Response:
        host = urlsplit(url).netloc
        with self._lock_for(host):
            if not self._allowed(url):
                raise FetchError(f"robots.txt disallows {url}")
            err: Exception | None = None
            interval = self._interval_for(url)
            for attempt in range(retries + 1):
                self._wait_turn(host, interval)
                try:
                    resp = self._raw_get(url, host, **kw)
                except requests.RequestException as e:
                    err = e
                else:
                    if resp.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                        err = FetchError(f"HTTP {resp.status_code}")
                    elif resp.status_code >= 400:
                        raise FetchError(f"HTTP {resp.status_code} for {url}")
                    else:
                        return resp
                time.sleep(2 * (attempt + 1))
            raise FetchError(f"{type(err).__name__}: {err} for {url}")

    def text(self, url: str, **kw) -> str:
        resp = self.get(url, **kw)
        resp.encoding = resp.encoding or "utf-8"
        return resp.text

    def json(self, url: str, **kw):
        return self.get(url, **kw).json()

"""Pace public market requests and share an upstream's rate-limit deadline."""
from datetime import timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
import re
import threading
import time
from tempfile import NamedTemporaryFile
from urllib.error import HTTPError


class MarketRateLimit(RuntimeError):
    pass


class PublicRequestGate:
    def __init__(self, interval, retry_path=None):
        self.interval = interval
        self.retry_path = retry_path
        self.lock = threading.Lock()
        self.next_request = 0.0
        self.blocked_until = 0.0
        self.retry_ms = None
        if retry_path:
            try:
                retry_ms = int(retry_path.read_text(encoding='ascii'))
                remaining = retry_ms - int(time.time()*1000)
                if remaining>0:
                    self.retry_ms = retry_ms
                    self.blocked_until = time.monotonic()+remaining/1000
            except (OSError, ValueError):
                pass

    def retry_at(self):
        with self.lock:
            return self.retry_ms if time.monotonic() < self.blocked_until else None

    def call(self, request, *args, **kwargs):
        while True:
            with self.lock:
                now = time.monotonic()
                if now < self.blocked_until:
                    raise MarketRateLimit()
                wait = self.next_request - now
                if wait <= 0:
                    self.next_request = now + self.interval
                    break
            time.sleep(wait)
        try:
            return request(*args, **kwargs)
        except (HTTPError, RuntimeError) as error:
            response = error if isinstance(error, HTTPError) else error.__cause__
            if not isinstance(response, HTTPError) or response.code not in (418, 429):
                raise
            now = time.time()
            retry = response.headers.get('Retry-After', '').strip() if response.headers else ''
            delay = 300 if response.code == 418 else 60
            if retry.isdecimal():
                delay = max(1, int(retry))
            elif retry:
                try:
                    until = parsedate_to_datetime(retry)
                    if until.tzinfo is None:
                        until = until.replace(tzinfo=timezone.utc)
                    delay = max(1, until.timestamp() - now)
                except (TypeError, ValueError, OverflowError):
                    pass
            elif response.code == 418:
                until = re.search(r'banned until (\d{13})', str(error), flags=re.I)
                if until:
                    delay = max(delay, int(until[1]) / 1000 - now)
            with self.lock:
                deadline = time.monotonic() + delay
                if deadline > self.blocked_until:
                    self.blocked_until = deadline
                    self.retry_ms = int((now + delay) * 1000)
                    self.save_retry()
            raise MarketRateLimit() from error

    def save_retry(self):
        if self.retry_path is None:
            return
        try:
            with NamedTemporaryFile(mode='w', encoding='ascii', dir=self.retry_path.parent,
                                    delete=False) as stream:
                temporary = Path(stream.name)
                try:
                    stream.write(str(self.retry_ms))
                    stream.close()
                    temporary.replace(self.retry_path)
                finally:
                    stream.close()
                    temporary.unlink(missing_ok=True)
        except OSError:
            pass

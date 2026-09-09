"""Read-only FHIR transport and search results with source metadata."""
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Optional, Tuple
from urllib.parse import unquote, urljoin, urlsplit

import requests

from .auth import BackendAuth
from .errors import (AuthenticationError, AuthorizationError, FHIRHTTPError,
                     NotFoundError, PaginationError, ProtocolError, RateLimitError,
                     TransportError)


@dataclass(frozen=True)
class SearchEntry:
    resource: dict = field(repr=False)
    full_url: Optional[str] = field(default=None, repr=False)
    mode: Optional[str] = None
    score: Optional[float] = None


@dataclass(frozen=True)
class SearchPage:
    source_url: str = field(repr=False)
    entries: Tuple[SearchEntry, ...] = field(repr=False)
    total: Optional[int] = None
    bundle_id: Optional[str] = field(default=None, repr=False)
    timestamp: Optional[str] = None
    links: tuple = field(default=(), repr=False)

    @property
    def next_url(self):
        return next((link["url"] for link in self.links if link["relation"] == "next"), None)


@dataclass(frozen=True)
class SearchResult:
    pages: Tuple[SearchPage, ...] = field(repr=False)
    # Means all returned pages were fetched, not that an entire chart was accessible.
    traversal_complete: bool = True

    @property
    def entries(self):
        return tuple(entry for page in self.pages for entry in page.entries)

    @property
    def matches(self):
        return [e.resource for e in self.entries if e.mode in (None, "match")]

    @property
    def included(self):
        return [e.resource for e in self.entries if e.mode == "include"]

    @property
    def outcomes(self):
        return [e.resource for e in self.entries if e.mode == "outcome"]

    @property
    def has_errors(self):
        return any(isinstance(issue, dict) and issue.get("severity") in ("fatal", "error")
                   for outcome in self.outcomes
                   if isinstance(outcome.get("issue"), list)
                   for issue in outcome["issue"])


def _retry_after(value):
    if not isinstance(value, str):
        return None
    try:
        if value.isdigit():
            return int(value)
        when = parsedate_to_datetime(value)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return max(0, (when - datetime.now(timezone.utc)).total_seconds())
    except (ValueError, TypeError, OverflowError):
        return None


class FHIRClient:
    def __init__(self, settings, auth=None, session=None, *, sleeper=time.sleep):
        self.settings = settings
        self.base_url = settings.base_url.rstrip("/") + "/"
        self.auth = auth if auth is not None else BackendAuth(settings)
        self._owns_auth = auth is None
        self.session = session if session is not None else requests.Session()
        self._owns_session = session is None
        self._sleep = sleeper

    def _url(self, path):
        if not isinstance(path, str) or any(c.isspace() or ord(c) < 32 for c in path) or "\\" in path:
            raise ValueError("Invalid FHIR request path.")
        # Validate before urljoin normalizes dot segments; reject repeated encoding.
        raw_path = unquote(urlsplit(path).path)
        if ("%" in raw_path or "\\" in raw_path or any(c.isspace() or ord(c) < 32 for c in raw_path)
                or any(p in (".", "..") for p in raw_path.split("/"))):
            raise ValueError("FHIR links must stay inside the configured FHIR base.")
        url = urljoin(self.base_url, path)
        base, target = urlsplit(self.base_url), urlsplit(url)
        if (target.scheme, target.netloc) != (base.scheme, base.netloc):
            raise ValueError("FHIR links must stay on the configured server.")
        if not unquote(target.path).startswith(unquote(base.path)) or target.fragment or target.username:
            raise ValueError("FHIR links must stay inside the configured FHIR base.")
        return url

    def _delay(self, retry, retry_after=None):
        if retry_after is not None:
            # Do not retry earlier than a long Retry-After or wait without a bound.
            return retry_after if retry_after <= self.settings.max_retry_delay else None
        return min(self.settings.max_retry_delay, 2 ** retry + random.random())

    def get(self, path, params=None):
        url = self._url(path)  # validate before acquiring credentials
        retries, refreshed = 0, False
        while True:
            token = self.auth.token()
            try:
                response = self.session.get(
                    url, params=params,
                    headers={"Authorization": f"Bearer {token}", "Accept": "application/fhir+json"},
                    timeout=self.settings.timeout, allow_redirects=False,
                )
            except (requests.Timeout, requests.ConnectionError):
                if retries >= self.settings.max_retries:
                    raise TransportError("FHIR server could not be reached within the retry limit.") from None
                self._sleep(self._delay(retries))
                retries += 1
                continue
            except requests.RequestException:
                raise TransportError("FHIR request could not be sent.") from None
            status = response.status_code
            if status == 200:
                try:
                    result = response.json()
                except ValueError:
                    raise ProtocolError("FHIR server returned invalid JSON.") from None
                if not isinstance(result, dict):
                    raise ProtocolError("Expected a FHIR JSON object.")
                return result
            if status == 401:
                if not refreshed and callable(getattr(self.auth, "invalidate", None)):
                    self.auth.invalidate(token)
                    refreshed = True
                    continue
                raise AuthenticationError("FHIR server rejected the access token (HTTP 401).", 401)
            retry_after = _retry_after(response.headers.get("Retry-After"))
            if status in (429, 502, 503, 504) and retries < self.settings.max_retries:
                delay = self._delay(retries, retry_after)
                if delay is not None:
                    self._sleep(delay)
                    retries += 1
                    continue
            try:
                body = response.json()
                outcome = body if isinstance(body, dict) and body.get("resourceType") == "OperationOutcome" else None
            except ValueError:
                outcome = None
            error = {403: AuthorizationError, 404: NotFoundError, 429: RateLimitError}.get(status, FHIRHTTPError)
            raise error(status, outcome=outcome, retry_after=retry_after)

    @staticmethod
    def _page(bundle, source_url):
        if bundle.get("resourceType") != "Bundle" or bundle.get("type", "searchset") != "searchset":
            raise ProtocolError("Expected a FHIR search Bundle.")
        try:
            entries = []
            for item in bundle.get("entry", []):
                resource = item.get("resource")
                if resource is None:
                    continue
                if not isinstance(resource, dict):
                    raise ValueError
                search = item.get("search", {})
                mode = search.get("mode")
                if resource.get("resourceType") == "OperationOutcome":
                    mode = "outcome"
                if mode not in (None, "match", "include", "outcome"):
                    raise ValueError
                entries.append(SearchEntry(resource, item.get("fullUrl"), mode, search.get("score")))
            links = tuple(bundle.get("link", []))
            if any(not isinstance(link.get("url"), str) or not isinstance(link.get("relation"), str) for link in links):
                raise ValueError
            if sum(link["relation"] == "next" for link in links) > 1:
                raise ValueError
            total = bundle.get("total")
            if total is not None and (type(total) is not int or total < 0):
                raise ValueError
            return SearchPage(source_url, tuple(entries), total, bundle.get("id"), bundle.get("timestamp"), links)
        except (ValueError, TypeError, AttributeError, KeyError):
            raise ProtocolError("Malformed FHIR search Bundle.") from None

    def search_pages(self, resource, params=None, max_pages=20):
        if type(max_pages) is not int or max_pages < 1:
            raise ValueError("max_pages must be positive.")
        path, seen = resource, set()
        for index in range(max_pages):
            url = self._url(path)
            page_params = params if index == 0 else None
            prepared = requests.Request("GET", url, params=page_params).prepare().url
            if prepared in seen:
                raise PaginationError("FHIR pagination loop detected.")
            seen.add(prepared)
            page = self._page(self.get(path, params=page_params), prepared)
            yield page
            if not page.next_url:
                return
            # Relative links resolve against the actual page URL, including query-only links.
            self._url(page.next_url)
            path = urljoin(url, page.next_url)
        raise PaginationError("FHIR page limit reached; narrow the search or increase max_pages.")

    def search_result(self, resource, params=None, max_pages=20):
        """Collect all pages or raise; never return a partial success result."""
        return SearchResult(tuple(self.search_pages(resource, params, max_pages)))

    def search(self, resource, params=None, max_pages=20):
        """Compatibility resource iterator. Use search_result to retain metadata."""
        for page in self.search_pages(resource, params, max_pages):
            for entry in page.entries:
                yield entry.resource

    def close(self):
        if self._owns_session:
            self.session.close()
        if self._owns_auth:
            self.auth.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

"""Pluggable browser-side validation for market URLs and page markers."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol
from urllib.parse import urlparse
import re

from validators.url_validation import compare_market_urls

BROWSER_STATUS_PASS = "PASS"
BROWSER_STATUS_FAIL = "FAIL"
BROWSER_STATUS_BLOCKED = "BLOCKED"
BROWSER_STATUS_STUB = "STUB"

_BLOCKING_MARKERS: dict[str, str] = {
    "login": "login_required",
    "captcha": "captcha",
    "geo_restricted": "geo_restricted",
    "anti_bot": "anti_bot",
    "site_outage": "site_outage",
}
_PROTOCOL_ALIASES: dict[str, tuple[str, ...]] = {
    "aave_v3": ("aave", "app.aave.com"),
    "morpho": ("morpho", "app.morpho.org"),
    "euler_v2": ("euler", "app.euler.finance"),
    "venus": ("venus", "app.venus.io", "venus.io"),
    "fluid": ("fluid", "fluid.io"),
    "termmax": ("termmax", "term structure", "app.termmax.ts.finance"),
    "lista": ("lista", "lista dao", "lista.org", "lista.dao"),
    "pendle": ("pendle", "app.pendle.finance"),
}
_CHAIN_ALIASES: dict[str, tuple[str, ...]] = {
    "ethereum": ("ethereum", "mainnet"),
    "arbitrum": ("arbitrum", "arbitrumone"),
    "base": ("base",),
    "optimism": ("optimism",),
    "polygon": ("polygon",),
    "avalanche": ("avalanche",),
    "bnb": ("bnb", "bsc", "binance smart chain"),
    "gnosis": ("gnosis",),
    "scroll": ("scroll",),
    "sonic": ("sonic",),
    "mantle": ("mantle",),
    "xlayer": ("x layer", "xlayer"),
}
_BLOCK_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("captcha", ("captcha", "verify you are human", "i am human")),
    ("anti_bot", ("cloudflare", "attention required", "bot detected", "access denied")),
    ("geo_restricted", ("not available in your region", "geo restricted", "unsupported region")),
    ("login", ("sign in", "log in", "login", "authenticate")),
    ("site_outage", ("service unavailable", "internal server error", "site is down")),
)


@dataclass(frozen=True)
class BrowserValidationOutcome:
    """Result of validating a market page (or URL) in a browser-like environment."""

    resolved_url: str
    status: str
    summary: str | None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BrowserPageSnapshot:
    """Normalized page inspection result returned by a browser inspector."""

    resolved_url: str
    page_markers: tuple[str, ...] = ()
    blocked_reason: str | None = None
    http_status: int | None = None
    title: str | None = None
    raw_metadata: dict[str, Any] = field(default_factory=dict)


class BrowserValidationAdapter(Protocol):
    """Implementations perform real browser checks; tests use lightweight fakes."""

    def validate_market_url(self, market: dict[str, Any]) -> BrowserValidationOutcome:
        """Validate ``market`` (expects ``market_url``, ``market_id`` when available)."""
        ...


class BrowserPageInspector(Protocol):
    """Low-level browser/page inspector used by real validation adapters."""

    def inspect_market_page(self, market: Mapping[str, Any]) -> BrowserPageSnapshot:
        """Open the page and return the resolved URL plus relevant markers."""
        ...

    def close(self) -> None:
        """Release any browser resources held by the inspector."""
        ...


def _normalize_markers(markers: tuple[str, ...] | list[str]) -> set[str]:
    return {str(marker).strip().lower() for marker in markers if str(marker).strip()}


def _contains_token(haystack: str, token: str) -> bool:
    escaped = re.escape(token.lower())
    return re.search(rf"(?<![a-z0-9]){escaped}(?![a-z0-9])", haystack) is not None


def _page_haystack(*parts: str | None) -> str:
    return " ".join(str(part or "").strip().lower() for part in parts if str(part or "").strip())


def _protocol_marker_found(protocol: str, haystack: str) -> bool:
    aliases = _PROTOCOL_ALIASES.get(protocol, (protocol,))
    return any(alias in haystack for alias in aliases)


def _chain_marker_found(chain_name: str, haystack: str) -> bool:
    aliases = _CHAIN_ALIASES.get(chain_name.lower(), (chain_name.lower(),))
    return any(alias in haystack for alias in aliases)


def _extract_page_markers(
    market: Mapping[str, Any],
    *,
    resolved_url: str,
    title: str | None,
    body_text: str,
) -> tuple[str, ...]:
    haystack = _page_haystack(resolved_url, title, body_text)
    markers: set[str] = set()
    protocol = str(market.get("protocol") or "").strip().lower()
    if protocol and _protocol_marker_found(protocol, haystack):
        markers.add(protocol)
    chain_name = str(market.get("chain_name") or "").strip().lower()
    if chain_name and _chain_marker_found(chain_name, haystack):
        markers.add(chain_name)
    symbol = str(((market.get("debt_token") or {}).get("symbol") or market.get("symbol") or "")).strip().lower()
    if symbol and (_contains_token(haystack, symbol) or symbol in haystack):
        markers.add(symbol)
    for marker, phrases in _BLOCK_PATTERNS:
        if any(phrase in haystack for phrase in phrases):
            markers.add(marker)
    return tuple(sorted(markers))


def _detect_blocked_reason(
    *,
    resolved_url: str,
    title: str | None,
    body_text: str,
    http_status: int | None,
) -> str | None:
    haystack = _page_haystack(resolved_url, title, body_text)
    host = (urlparse(resolved_url).hostname or "").lower()
    for marker, phrases in _BLOCK_PATTERNS:
        if any(phrase in haystack for phrase in phrases):
            return _BLOCKING_MARKERS[marker]
    if host == "app.aave.com" and "legacy markets" in haystack and "reserve size" not in haystack:
        return "legacy_market_shell"
    if http_status in {401, 403, 429, 451}:
        return f"http_{http_status}"
    if http_status is not None and http_status >= 500:
        return "site_outage"
    return None


def _accept_unverified_vault_modal(page: Any, target_url: str) -> bool:
    host = (urlparse(target_url).hostname or "").lower()
    if host != "app.euler.finance":
        return False
    try:
        body_text = page.evaluate(
            "() => document.body ? (document.body.innerText || '') : ''"
        ) or ""
    except Exception:
        return False
    haystack = str(body_text).lower()
    if "unverified vault" not in haystack:
        return False
    try:
        yes_button = page.get_by_role("button", name="Yes")
        if yes_button.count() < 1:
            return False
        yes_button.first.click(timeout=5000)
        page.wait_for_timeout(1500)
        return True
    except Exception:
        return False


def _expected_markers_for_market(market: Mapping[str, Any]) -> set[str]:
    markers: set[str] = set()
    protocol = market.get("protocol")
    chain_name = market.get("chain_name")
    if protocol:
        protocol_text = str(protocol).strip().lower()
        markers.add(protocol_text)
        if protocol_text in {"fluid", "lista"}:
            return markers
    if chain_name:
        if str(protocol or "").strip().lower() == "venus":
            chain_name = None
        else:
            markers.add(str(chain_name).strip().lower())
    symbol = (market.get("debt_token") or {}).get("symbol") or market.get("symbol")
    if symbol:
        markers.add(str(symbol).strip().lower())
    return markers


class RealBrowserValidationAdapter:
    """Validate browser outcome using resolved URL, page markers, and blocker rules."""

    def __init__(
        self,
        inspector: BrowserPageInspector,
        *,
        required_markers_by_protocol: Mapping[str, tuple[str, ...] | list[str]] | None = None,
    ) -> None:
        self._inspector = inspector
        self._required_markers_by_protocol = required_markers_by_protocol or {}

    def validate_market_url(self, market: dict[str, Any]) -> BrowserValidationOutcome:
        snapshot = self._inspector.inspect_market_page(market)
        observed_markers = _normalize_markers(list(snapshot.page_markers))
        protocol = str(market.get("protocol") or "").strip()
        expected_url = str(market.get("market_url") or "")
        required_markers = set(_expected_markers_for_market(market))
        required_markers.update(
            _normalize_markers(
                list(self._required_markers_by_protocol.get(protocol, ()))
            )
        )

        blocked_reason = snapshot.blocked_reason
        if blocked_reason is None:
            for marker, reason in _BLOCKING_MARKERS.items():
                if marker in observed_markers:
                    blocked_reason = reason
                    break
        if blocked_reason is None and snapshot.http_status in {401, 403, 429, 451, 503}:
            blocked_reason = f"http_{snapshot.http_status}"

        url_cmp = compare_market_urls(expected_url, snapshot.resolved_url)
        missing_markers = sorted(required_markers - observed_markers)

        metadata = {
            **snapshot.raw_metadata,
            "resolved_url": snapshot.resolved_url,
            "http_status": snapshot.http_status,
            "title": snapshot.title,
            "page_markers": sorted(observed_markers),
            "required_markers": sorted(required_markers),
            "missing_markers": missing_markers,
            "url_bucket": url_cmp["bucket"],
            "blocked_reason": blocked_reason,
        }

        if blocked_reason:
            return BrowserValidationOutcome(
                resolved_url=snapshot.resolved_url,
                status=BROWSER_STATUS_BLOCKED,
                summary=f"browser blocked: {blocked_reason}",
                metadata=metadata,
            )

        if url_cmp["bucket"] != "PASS" or missing_markers:
            summary_bits: list[str] = []
            if url_cmp["bucket"] != "PASS":
                summary_bits.append(f"url mismatch ({url_cmp['bucket']})")
            if missing_markers:
                summary_bits.append(f"missing markers: {', '.join(missing_markers)}")
            return BrowserValidationOutcome(
                resolved_url=snapshot.resolved_url,
                status=BROWSER_STATUS_FAIL,
                summary="; ".join(summary_bits) or "browser validation failed",
                metadata=metadata,
            )

        return BrowserValidationOutcome(
            resolved_url=snapshot.resolved_url,
            status=BROWSER_STATUS_PASS,
            summary="browser page markers matched expected market",
            metadata=metadata,
        )

    def close(self) -> None:
        close = getattr(self._inspector, "close", None)
        if callable(close):
            close()


class EchoStubBrowserAdapter:
    """Smoke adapter: echoes ``market_url`` as ``resolved_url`` without external I/O."""

    def validate_market_url(self, market: dict[str, Any]) -> BrowserValidationOutcome:
        raw = market.get("market_url") or ""
        return BrowserValidationOutcome(
            resolved_url=raw,
            status=BROWSER_STATUS_STUB,
            summary="echo stub (no browser)",
            metadata={"adapter": "EchoStubBrowserAdapter"},
        )


class PlaywrightBrowserInspector:
    """Real browser inspector backed by Playwright Chromium."""

    def __init__(
        self,
        *,
        headless: bool = True,
        timeout_ms: int = 20000,
        wait_after_load_ms: int = 3000,
        artifacts_dir: str | Path | None = None,
        reuse_sessions: bool = True,
    ) -> None:
        self._headless = headless
        self._timeout_ms = timeout_ms
        self._wait_after_load_ms = wait_after_load_ms
        self._artifacts_dir = Path(artifacts_dir) if artifacts_dir is not None else None
        self._reuse_sessions = reuse_sessions
        self._playwright_cm: Any | None = None
        self._playwright: Any | None = None
        self._browser: Any | None = None
        self._contexts_by_host: dict[str, Any] = {}

    def inspect_market_page(self, market: Mapping[str, Any]) -> BrowserPageSnapshot:
        target_url = str(market.get("market_url") or "").strip()
        if not target_url:
            raise ValueError("browser validation requires market_url")
        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "playwright is not installed; run `pip install -e '.[dev]'` and "
                "`playwright install chromium` first"
            ) from exc

        screenshot_path = self._screenshot_path_for_market(market)
        page = None
        try:
            page = self._page_for_url(target_url, sync_playwright)
            response = page.goto(target_url, wait_until="domcontentloaded", timeout=self._timeout_ms)
            _accept_unverified_vault_modal(page, target_url)
            self._wait_for_page_ready(page)
            _accept_unverified_vault_modal(page, target_url)
            title = page.title()
            body_text = page.evaluate(
                "() => document.body ? (document.body.innerText || '') : ''"
            ) or ""
            resolved_url = page.url or target_url
            if screenshot_path is not None:
                screenshot_path.parent.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(screenshot_path), full_page=True)
            http_status = response.status if response is not None else None
            page_markers = _extract_page_markers(
                market,
                resolved_url=resolved_url,
                title=title,
                body_text=body_text,
            )
            blocked_reason = _detect_blocked_reason(
                resolved_url=resolved_url,
                title=title,
                body_text=body_text,
                http_status=http_status,
            )
            return BrowserPageSnapshot(
                resolved_url=resolved_url,
                page_markers=page_markers,
                blocked_reason=blocked_reason,
                http_status=http_status,
                title=title,
                raw_metadata={
                    "adapter": "PlaywrightBrowserInspector",
                    "headless": self._headless,
                    "reuse_sessions": self._reuse_sessions,
                    "body_excerpt": body_text[:1000],
                    "body_length": len(body_text),
                    "screenshot_path": str(screenshot_path) if screenshot_path is not None else None,
                    "hostname": urlparse(resolved_url).hostname,
                },
            )
        except PlaywrightTimeoutError:
            return BrowserPageSnapshot(
                resolved_url=target_url,
                page_markers=(),
                blocked_reason="timeout",
                http_status=None,
                title=None,
                raw_metadata={
                    "adapter": "PlaywrightBrowserInspector",
                    "timeout_ms": self._timeout_ms,
                    "reuse_sessions": self._reuse_sessions,
                    "screenshot_path": str(screenshot_path) if screenshot_path is not None else None,
                },
            )
        except PlaywrightError as exc:
            return BrowserPageSnapshot(
                resolved_url=target_url,
                page_markers=(),
                blocked_reason="site_outage",
                http_status=None,
                title=None,
                raw_metadata={
                    "adapter": "PlaywrightBrowserInspector",
                    "error": str(exc),
                    "reuse_sessions": self._reuse_sessions,
                    "screenshot_path": str(screenshot_path) if screenshot_path is not None else None,
                },
            )
        finally:
            if page is not None:
                try:
                    if not page.is_closed():
                        page.close()
                except Exception:
                    pass

    def _screenshot_path_for_market(self, market: Mapping[str, Any]) -> Path | None:
        if self._artifacts_dir is None:
            return None
        market_id = str(market.get("market_id") or "unknown").strip().replace("/", "_")
        return self._artifacts_dir / f"{market_id}.png"

    def _page_for_url(self, target_url: str, sync_playwright: Any) -> Any:
        if not self._reuse_sessions:
            playwright_cm = sync_playwright()
            playwright = playwright_cm.__enter__()
            browser = playwright.chromium.launch(headless=self._headless)
            context = browser.new_context(ignore_https_errors=True)
            page = context.new_page()
            page.on("close", lambda: self._close_ephemeral(playwright_cm, browser, context))
            return page

        self._ensure_browser(sync_playwright)
        host = (urlparse(target_url).hostname or "").lower() or "__default__"
        context = self._contexts_by_host.get(host)
        if context is None:
            context = self._browser.new_context(ignore_https_errors=True)
            self._contexts_by_host[host] = context
        return context.new_page()

    def _ensure_browser(self, sync_playwright: Any) -> None:
        if self._browser is not None:
            return
        self._playwright_cm = sync_playwright()
        self._playwright = self._playwright_cm.__enter__()
        self._browser = self._playwright.chromium.launch(headless=self._headless)

    def _wait_for_page_ready(self, page: Any) -> None:
        try:
            page.wait_for_load_state("networkidle", timeout=min(self._timeout_ms, 5000))
        except Exception:
            pass
        if self._wait_after_load_ms > 0:
            page.wait_for_timeout(self._wait_after_load_ms)

    def _close_ephemeral(self, playwright_cm: Any, browser: Any, context: Any) -> None:
        try:
            context.close()
        except Exception:
            pass
        try:
            browser.close()
        except Exception:
            pass
        try:
            playwright_cm.__exit__(None, None, None)
        except Exception:
            pass

    def close(self) -> None:
        for context in self._contexts_by_host.values():
            try:
                context.close()
            except Exception:
                pass
        self._contexts_by_host.clear()
        if self._browser is not None:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._playwright_cm is not None:
            try:
                self._playwright_cm.__exit__(None, None, None)
            except Exception:
                pass
            self._playwright_cm = None
            self._playwright = None

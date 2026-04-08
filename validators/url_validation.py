"""URL normalization and expected-vs-resolved comparison for protocol validation."""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


def _normalize_fragment(fragment: str) -> str:
    raw = str(fragment or "").strip()
    if not raw:
        return ""
    frag_path, sep, frag_query = raw.partition("?")
    normalized_path = (frag_path or "/").rstrip("/") or "/"
    normalized_query = ""
    if sep:
        query_pairs = parse_qsl(frag_query, keep_blank_values=True)
        normalized_query = urlencode(sorted(query_pairs), doseq=True)
    if normalized_query:
        return f"{normalized_path}?{normalized_query}"
    return normalized_path


def normalize_url(url: str | None) -> str:
    """Lowercase scheme/host, normalize path/query/fragment for stable compare."""
    if url is None:
        return ""
    s = str(url).strip()
    if not s:
        return ""
    parsed = urlparse(s)
    path = parsed.path or "/"
    path = path.rstrip("/") or "/"
    netloc = parsed.netloc.lower()
    scheme = parsed.scheme.lower()
    query_pairs = parse_qsl(parsed.query, keep_blank_values=True)
    query = urlencode(sorted(query_pairs), doseq=True)
    fragment = _normalize_fragment(parsed.fragment)
    return urlunparse((scheme, netloc, path, "", query, fragment))


def compare_market_urls(
    expected_url: str | None,
    resolved_url: str | None,
) -> dict[str, Any]:
    """
    Compare official expected URL vs backend-resolved `market_url`.

    - Normalized equality -> PASS
    - Both empty -> PASS (nothing to validate)
    - One side empty -> BUG
    - Same scheme+netloc but different path/query/fragment -> WARN (possible redirect / variant)
    - Otherwise -> BUG
    """
    exp = normalize_url(expected_url)
    res = normalize_url(resolved_url)
    if not exp and not res:
        return {
            "bucket": "PASS",
            "expected": expected_url,
            "resolved": resolved_url,
            "normalized_expected": exp,
            "normalized_resolved": res,
            "detail": "both_empty",
        }
    if (not exp) ^ (not res):
        return {
            "bucket": "BUG",
            "expected": expected_url,
            "resolved": resolved_url,
            "normalized_expected": exp,
            "normalized_resolved": res,
            "detail": "asymmetric_empty",
        }
    if exp == res:
        return {
            "bucket": "PASS",
            "expected": expected_url,
            "resolved": resolved_url,
            "normalized_expected": exp,
            "normalized_resolved": res,
            "detail": "match",
        }
    pe, pr = urlparse(exp), urlparse(res)
    same_origin = pe.scheme == pr.scheme and pe.netloc == pr.netloc
    if same_origin and (pe.path != pr.path or pe.query != pr.query or pe.fragment != pr.fragment):
        return {
            "bucket": "WARN",
            "expected": expected_url,
            "resolved": resolved_url,
            "normalized_expected": exp,
            "normalized_resolved": res,
            "detail": "same_origin_diff_location",
        }
    return {
        "bucket": "BUG",
        "expected": expected_url,
        "resolved": resolved_url,
        "normalized_expected": exp,
        "normalized_resolved": res,
        "detail": "host_or_scheme_mismatch",
    }

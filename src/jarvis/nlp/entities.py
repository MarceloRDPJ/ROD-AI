"""Extract structured entities before natural-language normalization.

Network addresses have structural meaning. Removing dots or colons before
extracting them turns a valid command into ambiguous text, so this module runs
on the original user input and returns only validated values.
"""

from __future__ import annotations

from ipaddress import IPv4Address
import re
from typing import Any
from urllib.parse import urlsplit


_IPV4_CANDIDATE = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
_MAC_CANDIDATE = re.compile(r"(?<![0-9a-f])(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}(?![0-9a-f])", re.I)
_URL_CANDIDATE = re.compile(r"https?://[^\s]+", re.I)
_DOMAIN_CANDIDATE = re.compile(
    r"(?<![\w@])(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}(?![\w-])",
    re.I,
)


def _valid_ipv4(value: str) -> str | None:
    try:
        return str(IPv4Address(value))
    except ValueError:
        return None


def _clean_domain(value: str) -> str | None:
    domain = value.strip().lower().rstrip(".,;:!?)\"]}")
    if domain.startswith(("http://", "https://")):
        domain = (urlsplit(domain).hostname or "").lower()
    if domain.startswith("www."):
        domain = domain[4:]
    if not domain or not _DOMAIN_CANDIDATE.fullmatch(domain):
        return None
    return domain


def extract_structured_entities(text: str) -> dict[str, Any]:
    """Extract IP, MAC, URL and domain values, preserving input order."""
    if not isinstance(text, str):
        return {"ips": [], "macs": [], "domains": [], "urls": []}

    ips = [ip for match in _IPV4_CANDIDATE.findall(text) if (ip := _valid_ipv4(match))]
    macs = [match.lower().replace("-", ":") for match in _MAC_CANDIDATE.findall(text)]
    urls = [match.rstrip(".,;:!?)\"]}") for match in _URL_CANDIDATE.findall(text)]

    domains: list[str] = []
    for value in [*urls, *_DOMAIN_CANDIDATE.findall(text)]:
        domain = _clean_domain(value)
        if domain and domain not in domains:
            domains.append(domain)

    return {
        "ip": ips[0] if ips else None,
        "ips": list(dict.fromkeys(ips)),
        "mac": macs[0] if macs else None,
        "macs": list(dict.fromkeys(macs)),
        "domain": domains[0] if domains else None,
        "domains": domains,
        "url": urls[0] if urls else None,
        "urls": urls,
    }

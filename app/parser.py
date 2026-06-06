from dataclasses import dataclass
from urllib.parse import urljoin, urlparse
import re

import httpx
from bs4 import BeautifulSoup


@dataclass
class ParsedUnit:
    external_id: str
    floor_plan: str
    unit_name: str
    rent: int | None
    beds: float | None
    baths: float | None
    available_date: str
    unit_url: str


def parse_source_url(url: str) -> list[ParsedUnit]:
    domain = urlparse(url).netloc.lower()
    if domain.endswith("livelume.com"):
        return parse_lume(url)
    return []


def parse_lume(url: str) -> list[ParsedUnit]:
    floorplans_url = "https://livelume.com/floorplans/"
    with httpx.Client(follow_redirects=True, timeout=20) as client:
        listing_html = client.get(floorplans_url).text
        detail_urls = _find_lume_floorplan_urls(listing_html, floorplans_url)
        units = []
        for detail_url in detail_urls:
            detail_html = client.get(detail_url).text
            unit = _parse_lume_floorplan_detail(detail_url, detail_html)
            if unit:
                units.append(unit)
        return units


def _find_lume_floorplan_urls(html: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    urls = set()
    for link in soup.find_all("a", href=True):
        href = link["href"]
        if not re.fullmatch(r"/floorplans/[a-z0-9-]+/", href):
            continue
        if href.endswith("/ebrochure/"):
            continue
        urls.add(urljoin(base_url, href))
    return sorted(urls)


def _parse_lume_floorplan_detail(url: str, html: str) -> ParsedUnit | None:
    soup = BeautifulSoup(html, "html.parser")
    headings = [h.get_text(" ", strip=True) for h in soup.find_all("h1")]
    floor_plan = next((heading for heading in reversed(headings) if heading != "Floor Plans"), "")
    if not floor_plan:
        return None

    lines = [line.strip() for line in soup.get_text("\n").splitlines() if line.strip()]
    try:
        start = lines.index(floor_plan)
    except ValueError:
        start = 0

    detail_lines = lines[start : start + 12]
    beds = _parse_beds(detail_lines)
    baths = _parse_baths(detail_lines)
    rent = _parse_rent(detail_lines)
    availability = _parse_availability(detail_lines)

    if rent is None or availability == "Contact Us":
        return None

    return ParsedUnit(
        external_id=f"lume:{floor_plan.lower()}",
        floor_plan=floor_plan,
        unit_name=floor_plan,
        rent=rent,
        beds=beds,
        baths=baths,
        available_date=availability,
        unit_url=url,
    )


def _parse_beds(lines: list[str]) -> float | None:
    for line in lines:
        if line.lower() == "studio":
            return 0
        match = re.search(r"(\d+(?:\.\d+)?)\s*bed", line, re.IGNORECASE)
        if match:
            return float(match.group(1))
    return None


def _parse_baths(lines: list[str]) -> float | None:
    for line in lines:
        match = re.search(r"(\d+(?:\.\d+)?)\s*bath", line, re.IGNORECASE)
        if match:
            return float(match.group(1))
    return None


def _parse_rent(lines: list[str]) -> int | None:
    for line in lines:
        if "/mo" not in line and "Base Rent" not in line:
            continue
        prices = re.findall(r"\$([0-9,]+)(?:\.\d+)?", line)
        if prices:
            return int(prices[0].replace(",", ""))
    return None


def _parse_availability(lines: list[str]) -> str:
    for line in lines:
        if re.search(r"only\s+\d+\s+left", line, re.IGNORECASE):
            return line
    if any(line == "Check Availability" for line in lines):
        return "Check Availability"
    if any(line == "Contact Us" for line in lines):
        return "Contact Us"
    return "Available"


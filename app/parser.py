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
    if domain.endswith("antonmenlo.com"):
        return parse_anton_menlo(url)
    return []


def parse_lume(url: str) -> list[ParsedUnit]:
    floorplans_url = "https://livelume.com/floorplans/"
    with httpx.Client(follow_redirects=True, timeout=20) as client:
        listing_response = client.get(floorplans_url)
        listing_response.raise_for_status()
        listing_html = listing_response.text
        detail_urls = _find_lume_floorplan_urls(listing_html, floorplans_url)
        units = []
        for detail_url in detail_urls:
            detail_response = client.get(detail_url)
            detail_response.raise_for_status()
            detail_html = detail_response.text
            unit = _parse_lume_floorplan_detail(detail_url, detail_html)
            if unit:
                units.append(unit)
        return units


def parse_anton_menlo(url: str) -> list[ParsedUnit]:
    floorplans_url = "https://www.rentcafe.com/apartments/ca/menlo-park/anton-menlo/default.aspx"
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; MonitorRentals/1.0)",
    }
    with httpx.Client(follow_redirects=True, headers=headers, timeout=20) as client:
        listing_response = client.get(floorplans_url)
        listing_response.raise_for_status()
        return _parse_anton_rentcafe_listing(floorplans_url, listing_response.text)


def _parse_anton_rentcafe_listing(url: str, html: str) -> list[ParsedUnit]:
    soup = BeautifulSoup(html, "html.parser")
    lines = [line.strip() for line in soup.get_text("\n").splitlines() if line.strip()]
    units = []
    floor_plan = ""
    beds = None
    baths = None

    for index, line in enumerate(lines):
        if re.fullmatch(r"Residence\s+\d+", line):
            floor_plan = line
            beds, baths = _parse_anton_bed_bath(lines[index : index + 6])
            continue

        if not floor_plan:
            continue

        unit_match = re.match(
            r"^([A-Za-z0-9-]+)\s+\$([0-9,]+)(?:\s*-\s*\$?[0-9,]+)?\s+(.+)$",
            line,
        )
        if not unit_match:
            continue

        unit_name = f"#{unit_match.group(1)}"
        rent = int(unit_match.group(2).replace(",", ""))
        availability = unit_match.group(3).strip()
        units.append(
            ParsedUnit(
                external_id=f"antonmenlo:{floor_plan.lower()}:{unit_name.lower()}",
                floor_plan=floor_plan,
                unit_name=unit_name,
                rent=rent,
                beds=beds,
                baths=baths,
                available_date="Available" if availability == "Now" else availability,
                unit_url=url,
            )
        )

    return units


def _find_anton_floorplan_urls(html: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    urls = set()
    for link in soup.find_all("a", href=True):
        href = link["href"]
        if not re.search(r"/floorplans/residence-\d+/?$", href):
            continue
        urls.add(urljoin(base_url, href))
    return sorted(urls)


def _parse_anton_floorplan_detail(url: str, html: str) -> list[ParsedUnit]:
    soup = BeautifulSoup(html, "html.parser")
    lines = [line.strip() for line in soup.get_text("\n").splitlines() if line.strip()]
    floor_plan = _first_matching_line(lines, r"^Residence\s+\d+$") or _slug_title(url)
    beds, baths = _parse_anton_bed_bath(lines)
    apply_urls = _find_anton_apply_urls(soup, url)

    units = []
    for index, line in enumerate(lines):
        if line != "Apartment:":
            continue

        unit_name = _next_non_label_line(lines, index + 1)
        rent_line = _line_after_label(lines, index, "Rent:")
        date_line = _line_after_label(lines, index, "Date:")
        rent = _parse_first_price(rent_line or "")
        if not unit_name or rent is None:
            continue

        unit_url = apply_urls.get(unit_name, url)
        units.append(
            ParsedUnit(
                external_id=f"antonmenlo:{floor_plan.lower()}:{unit_name.lower()}",
                floor_plan=floor_plan,
                unit_name=unit_name,
                rent=rent,
                beds=beds,
                baths=baths,
                available_date=date_line or "Available",
                unit_url=unit_url,
            )
        )
    return units


def _parse_anton_bed_bath(lines: list[str]) -> tuple[float | None, float | None]:
    for line in lines:
        match = re.search(
            r"(\d+)\s+Bedroom[s]?\s+\|\s+(\d+)\s+Bathroom[s]?",
            line,
            re.IGNORECASE,
        )
        if match:
            return float(match.group(1)), float(match.group(2))
        match = re.search(
            r"(\d+)\s+Bed[s]?\s*/\s*(\d+)\s+Bath[s]?",
            line,
            re.IGNORECASE,
        )
        if match:
            return float(match.group(1)), float(match.group(2))
        match = re.search(
            r"Studio\s+\|\s+(\d+)\s+Bathroom[s]?",
            line,
            re.IGNORECASE,
        )
        if match:
            return 0, float(match.group(1))
        match = re.search(
            r"Studio\s*/\s*(\d+)\s+Bath[s]?",
            line,
            re.IGNORECASE,
        )
        if match:
            return 0, float(match.group(1))
    return None, None


def _find_anton_apply_urls(soup: BeautifulSoup, base_url: str) -> dict[str, str]:
    urls = {}
    for link in soup.find_all("a", href=True):
        text = link.get_text(" ", strip=True)
        match = re.search(r"apartment\s+(#[A-Za-z0-9-]+)", text, re.IGNORECASE)
        if match:
            urls[match.group(1)] = urljoin(base_url, link["href"])
    return urls


def _first_matching_line(lines: list[str], pattern: str) -> str | None:
    for line in lines:
        if re.search(pattern, line):
            return line
    return None


def _slug_title(url: str) -> str:
    slug = url.rstrip("/").split("/")[-1]
    return slug.replace("-", " ").title()


def _next_non_label_line(lines: list[str], start_index: int) -> str:
    labels = {"Apartment:", "Sq. Ft.:", "Rent:", "Deposit:", "Date:"}
    for line in lines[start_index : start_index + 4]:
        if line not in labels:
            return line
    return ""


def _line_after_label(lines: list[str], start_index: int, label: str) -> str:
    for index in range(start_index, min(start_index + 20, len(lines))):
        if lines[index] == label and index + 1 < len(lines):
            return lines[index + 1]
    return ""


def _parse_first_price(text: str) -> int | None:
    prices = re.findall(r"\$([0-9,]+)(?:\.\d+)?", text)
    if not prices:
        return None
    return int(prices[0].replace(",", ""))


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

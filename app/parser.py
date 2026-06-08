from dataclasses import dataclass
from datetime import date
from urllib.parse import urljoin, urlparse
import json
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
    if domain.endswith("livevasara.com") or domain.endswith("greystar.com"):
        return parse_vasara(url)
    raise ValueError(f"Unsupported source domain: {domain}")


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
    proxy_url = f"https://r.jina.ai/http://r.jina.ai/http://{floorplans_url}"
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; MonitorRentals/1.0)",
    }
    with httpx.Client(follow_redirects=True, headers=headers, timeout=20) as client:
        try:
            listing_response = client.get(floorplans_url)
            listing_response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 403:
                raise
            listing_response = client.get(proxy_url)
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
        floor_plan_match = re.fullmatch(r"\*{0,2}(Residence\s+\d+)\*{0,2}", line)
        if floor_plan_match:
            floor_plan = floor_plan_match.group(1)
            beds, baths = _parse_anton_bed_bath(lines[index : index + 6])
            continue

        if not floor_plan:
            continue

        table_unit_match = re.match(
            r"^\|\s*([A-Za-z0-9-]+)\s*\|\s*\$([0-9,]+)(?:\s*-\s*\$?[0-9,]+)?\s*\|\s*([^|]+)\|",
            line,
        )
        unit_match = table_unit_match or re.match(
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


def parse_vasara(url: str) -> list[ParsedUnit]:
    greystar_url = "https://www.greystar.com/vasara-menlo-park-ca/p_21147"
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; MonitorRentals/1.0)",
    }
    with httpx.Client(follow_redirects=True, headers=headers, timeout=20) as client:
        response = client.get(greystar_url)
        response.raise_for_status()
        return _parse_greystar_next_data(greystar_url, response.text, "vasara")


def _parse_greystar_next_data(url: str, html: str, source_key: str) -> list[ParsedUnit]:
    soup = BeautifulSoup(html, "html.parser")
    script = soup.find("script", id="__NEXT_DATA__")
    if not script or not script.string:
        return []

    data = json.loads(script.string)
    page_props = data.get("props", {}).get("pageProps", {})
    property_context = (
        page_props.get("page", {})
        .get("layout", {})
        .get("sitecore", {})
        .get("context", {})
        .get("property", {})
    )
    floorplans = {
        str(floorplan.get("id")): floorplan
        for floorplan in property_context.get("floorplans", [])
    }

    units = []
    for unit in page_props.get("propertyUnits", []):
        unit_number = str(unit.get("unitNumber") or "").strip()
        floor_plan = str(unit.get("floorPlanLabel") or "").strip()
        rent = unit.get("minPrice")
        if not unit_number or not floor_plan or rent is None:
            continue

        floorplan = floorplans.get(str(unit.get("floorPlanId")), {})
        available_on = str(unit.get("availableOn") or "").strip()
        unit_id = str(unit.get("unitId") or "").strip()
        unit_url = url
        if unit_id and available_on:
            unit_url = f"{url}/calculator?leaseTerm=12&moveInDate={available_on}&unitId={unit_id}"

        units.append(
            ParsedUnit(
                external_id=f"{source_key}:{floor_plan.lower()}:{unit_number.lower()}",
                floor_plan=floor_plan,
                unit_name=f"#{unit_number}",
                rent=int(rent),
                beds=_number_or_none(floorplan.get("bedroomCount")),
                baths=_number_or_none(floorplan.get("bathroomCount")),
                available_date=_format_available_on(available_on),
                unit_url=unit_url,
            )
        )

    return units


def _number_or_none(value: object) -> float | None:
    if value is None:
        return None
    return float(value)


def _format_available_on(value: str) -> str:
    if not value:
        return "Available"
    try:
        available_on = date.fromisoformat(value)
    except ValueError:
        return value
    if available_on <= date.today():
        return "Available"
    return available_on.strftime("%b %-d, %Y")


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

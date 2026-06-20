from dataclasses import dataclass
from datetime import date
from urllib.parse import quote, urljoin, urlparse
import hashlib
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

    candidate_url = _parser_url_for_source(url)
    html = _fetch_with_proxy_fallback(candidate_url)
    source_key = _source_key(url)

    units = _parse_greystar_next_data(candidate_url, html, source_key)
    if units:
        return units

    units = _parse_jonah_floorplans(candidate_url, html, source_key)
    if units:
        return units

    units = _parse_rentcafe_listing(candidate_url, html, source_key)
    if units:
        return units

    raise ValueError(f"Unsupported source domain or no units found: {domain}")


def _parser_url_for_source(url: str) -> str:
    domain = urlparse(url).netloc.lower()
    if domain.endswith("antonmenlo.com"):
        return "https://www.rentcafe.com/apartments/ca/menlo-park/anton-menlo/default.aspx"
    if domain.endswith("livevasara.com"):
        return "https://www.greystar.com/vasara-menlo-park-ca/p_21147"
    if domain.endswith("livelandsby.com") and not url.rstrip("/").endswith("/floorplans"):
        return "https://livelandsby.com/floorplans/"
    return url


def _fetch_with_proxy_fallback(url: str) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; MonitorRentals/1.0)",
    }
    with httpx.Client(follow_redirects=True, headers=headers, timeout=20) as client:
        try:
            response = client.get(url)
            response.raise_for_status()
            return response.text
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 403:
                raise
            proxy_response = client.get(_jina_proxy_url(url))
            proxy_response.raise_for_status()
            return proxy_response.text


def _jina_proxy_url(url: str) -> str:
    return f"https://r.jina.ai/http://r.jina.ai/http://{url}"


def _source_key(url: str) -> str:
    parsed = urlparse(url)
    domain = parsed.netloc.lower()
    if domain.startswith("www."):
        domain = domain[4:]
    if domain.endswith("antonmenlo.com"):
        return "antonmenlo"
    if domain.endswith("livevasara.com"):
        return "vasara"
    if domain.endswith("livelume.com"):
        return "lume"
    if domain.endswith("greystar.com"):
        slug = parsed.path.strip("/").split("/")[0]
        if slug:
            return re.sub(r"[^a-z0-9]+", "", slug.lower())
    return re.sub(r"[^a-z0-9]+", "", domain.split(".")[0])


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
    floorplans_url = _parser_url_for_source(url)
    return _parse_rentcafe_listing(
        floorplans_url,
        _fetch_with_proxy_fallback(floorplans_url),
        _source_key(url),
    )


def _parse_anton_rentcafe_listing(url: str, html: str) -> list[ParsedUnit]:
    return _parse_rentcafe_listing(url, html, "antonmenlo")


def _parse_rentcafe_listing(url: str, html: str, source_key: str) -> list[ParsedUnit]:
    soup = BeautifulSoup(html, "html.parser")
    lines = [line.strip() for line in soup.get_text("\n").splitlines() if line.strip()]
    units = []
    floor_plan = ""
    beds = None
    baths = None

    for index, line in enumerate(lines):
        floor_plan_candidate = _clean_heading_line(line)
        line_beds, line_baths = _parse_bed_bath([line])
        candidate_beds, candidate_baths = _parse_bed_bath(lines[index + 1 : index + 7])
        if floor_plan_candidate and candidate_beds is not None and candidate_baths is not None:
            if line_beds is not None or line_baths is not None:
                continue
            floor_plan = floor_plan_candidate
            beds = candidate_beds
            baths = candidate_baths
            continue

        if not floor_plan:
            continue

        if line == "Apartment:":
            unit_name = _next_non_label_line(lines, index + 1)
            rent_line = _line_after_label(lines, index, "Rent:")
            date_line = _line_after_label(lines, index, "Date:")
            rent = _parse_first_price(rent_line or "")
            if not unit_name or rent is None:
                continue
            if not unit_name.startswith("#"):
                unit_name = f"#{unit_name}"
            units.append(
                ParsedUnit(
                    external_id=f"{source_key}:{floor_plan.lower()}:{unit_name.lower()}",
                    floor_plan=floor_plan,
                    unit_name=unit_name,
                    rent=rent,
                    beds=beds,
                    baths=baths,
                    available_date=date_line or "Available",
                    unit_url=url,
                )
            )
            continue

        table_unit_match = re.match(
            r"^\|\s*#?([A-Za-z0-9-]+)\s*\|\s*\$([0-9,]+)(?:\s*-\s*\$?[0-9,]+)?\s*\|\s*([^|]+)\|",
            line,
        )
        unit_match = table_unit_match or re.match(
            r"^#?([A-Za-z0-9-]+)\s+\$([0-9,]+)(?:\s*-\s*\$?[0-9,]+)?\s+(.+)$",
            line,
        )
        if not unit_match:
            continue

        unit_name = f"#{unit_match.group(1)}"
        rent = int(unit_match.group(2).replace(",", ""))
        availability = unit_match.group(3).strip()
        units.append(
            ParsedUnit(
                external_id=f"{source_key}:{floor_plan.lower()}:{unit_name.lower()}",
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


def _clean_heading_line(line: str) -> str:
    cleaned = re.sub(r"^[#*\s]+|[*\s]+$", "", line).strip()
    blocked = {
        "",
        "floor plans",
        "floor plan details",
        "floor plan video",
        "unit availability filters",
        "apartment search result",
    }
    if cleaned.lower() in blocked:
        return ""
    if cleaned.startswith("|") or cleaned.startswith("!["):
        return ""
    return cleaned


def parse_vasara(url: str) -> list[ParsedUnit]:
    greystar_url = _parser_url_for_source(url)
    return _parse_greystar_next_data(
        greystar_url,
        _fetch_with_proxy_fallback(greystar_url),
        _source_key(url),
    )


def _parse_jonah_floorplans(url: str, html: str, source_key: str) -> list[ParsedUnit]:
    soup = BeautifulSoup(html, "html.parser")
    script = soup.find("script", id="jd-fp-data-script-app")
    if not script or not script.string:
        return []

    config = json.loads(script.string)
    base_uri = config.get("base_uri") or "/floorplans/"
    endpoint = config.get("renderable_endpoint") or "_fp-renderable"
    endpoint_url = urljoin(urljoin(url, base_uri), f"{endpoint.strip('/')}/")
    pathname = urlparse(urljoin(url, base_uri)).path
    instance = hashlib.md5(pathname.encode()).hexdigest()
    params = [f"instance={instance}", "action=render", "type=listing-chunks"]
    for filter_id in config.get("filter_ids") or []:
        params.append(f"ids[]={filter_id}")
    chunks_url = urljoin(url, endpoint_url) + quote("params:" + "&".join(params), safe="") + "/"

    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; MonitorRentals/1.0)",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": urljoin(url, base_uri),
    }
    with httpx.Client(follow_redirects=True, headers=headers, timeout=20) as client:
        response = client.get(chunks_url, params={"forcecache": 1})
        response.raise_for_status()

    chunks = BeautifulSoup(response.text, "html.parser")
    unit_data = _chunk_json(chunks, "unit-data")
    return _parse_jonah_units(url, unit_data, source_key)


def _chunk_json(soup: BeautifulSoup, key: str) -> list[dict]:
    chunk = soup.find(attrs={"data-chunk-key": key})
    if not chunk:
        return []
    text = chunk.get_text(strip=True)
    if not text:
        return []
    return json.loads(text)


def _parse_jonah_units(url: str, unit_data: list[dict], source_key: str) -> list[ParsedUnit]:
    units = []
    for unit in unit_data:
        unit_name = str(unit.get("title") or unit.get("apartment_number") or "").strip()
        floor_plan = str(unit.get("floorplan_title") or "").strip()
        rent = _parse_intish(unit.get("rent_min"))
        if not unit_name or not floor_plan or rent is None:
            continue
        if not unit_name.startswith("#"):
            unit_name = f"#{unit_name}"
        units.append(
            ParsedUnit(
                external_id=f"{source_key}:{floor_plan.lower()}:{unit_name.lower()}",
                floor_plan=floor_plan,
                unit_name=unit_name,
                rent=rent,
                beds=_parse_bedroom_value(unit.get("bedrooms")),
                baths=_number_or_none(unit.get("bathrooms")),
                available_date="Available",
                unit_url=urljoin(url, str(unit.get("permalink") or "")) or url,
            )
        )
    return units


def _parse_bedroom_value(value: object) -> float | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if text == "studio":
        return 0
    return _number_or_none(value)


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


def _parse_intish(value: object) -> int | None:
    if value is None:
        return None
    try:
        return round(float(str(value).replace(",", "")))
    except ValueError:
        return None


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
    beds, baths = _parse_bed_bath(lines)
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


def _parse_bed_bath(lines: list[str]) -> tuple[float | None, float | None]:
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

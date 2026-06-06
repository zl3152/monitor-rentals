from app.config import MAX_RENT, TARGET_CITIES


KEY_FIELDS = ("rent", "beds", "baths", "city", "property_type", "has_amenities")


def normalize_city(city: str | None) -> str:
    return (city or "").strip().lower()


def calculate_fit_label(property_data) -> str:
    missing_required = []
    for field in KEY_FIELDS:
        value = getattr(property_data, field, None)
        if value is None or value == "":
            missing_required.append(field)

    if missing_required:
        return "Needs review"

    city = normalize_city(property_data.city)
    property_type = (property_data.property_type or "").strip().lower()

    if property_data.status == "Unavailable":
        return "Not a fit"
    if property_type == "townhouse":
        return "Not a fit"
    if property_type != "apartment":
        return "Needs review"
    if city not in TARGET_CITIES:
        return "Not a fit"
    if property_data.rent and property_data.rent > MAX_RENT:
        return "Not a fit"
    if property_data.has_amenities is False:
        return "Not a fit"

    if (property_data.beds or 0) >= 2 and (property_data.baths or 0) >= 2:
        return "Great fit"
    if (property_data.beds or 0) >= 1 and (property_data.baths or 0) >= 1:
        return "Possible fit"

    return "Not a fit"


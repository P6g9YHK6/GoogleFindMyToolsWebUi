"""Substitutes fixed coordinates into SEMANTIC location readings that have a
configured mapping - see webui/settings_store.py's semantic_location_map.
Google never reports lat/lon for a SEMANTIC result (a tracker recognized as
being near a named smart-home device, e.g. "Nest Mini - Living Room"), so
without this every gate in policy.py and the send in custom.py just drops it.
"""

from NovaApi.ExecuteAction.LocateTracker.decrypt_locations import create_map_links


def _matches(entry_name: str, match_mode: str, semantic_name: str) -> bool:
    """"full" (default) requires an exact, case-sensitive match; "partial"
    only requires entry_name to appear anywhere in semantic_name,
    case-insensitively - Google's reported text isn't always identical
    across readings."""
    if match_mode == "partial":
        return entry_name.casefold() in semantic_name.casefold()
    return entry_name == semantic_name


def apply_semantic_mapping(locations: list[dict], mapping: dict) -> list[dict]:
    """Returns `locations` with a coordinate-bearing copy substituted for any
    entry whose is_semantic/semantic_name matches a mapping key (see
    _matches) - status/semantic_name/etc. are left as decoded, so a mapped
    reading still reads as semantic downstream. An unmatched location is
    passed through unchanged (same object). First match wins, in dict order."""
    if not mapping:
        return locations

    mapped = []
    for location in locations:
        name = (location.get("semantic_name") or "").strip()
        coords = None
        if location.get("is_semantic") and name:
            for entry_name, entry in mapping.items():
                if _matches(entry_name, entry.get("match_mode", "full"), name):
                    coords = entry
                    break
        if coords is None:
            mapped.append(location)
            continue

        latitude, longitude = coords.get("latitude"), coords.get("longitude")
        mapped.append({
            **location,
            "latitude": latitude,
            "longitude": longitude,
            "altitude": None,
            "map_links": create_map_links(latitude, longitude),
        })
    return mapped

from urllib.parse import quote


def build_datasette_url(meeting: str, date: str, page) -> str:
    """Returns URL to Datasette filtered view for this specific meeting page."""
    base = "https://denver.co.civic.band/meetings/minutes"
    return f"{base}?meeting={quote(meeting)}&date={date}&page={page}"


def build_image_url(meeting: str, date: str, page) -> str:
    """Returns URL to the scanned page image."""
    base = "https://denver.co.civic.band"
    return f"{base}/{meeting}/{date}/{page}.png"

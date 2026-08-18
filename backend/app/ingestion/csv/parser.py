"""
One generic parser for every channel — channel-specific quirks live only in
channel_configs.py's column_map, never here. This keeps the parser itself
trivial and means adding a 5th channel is a config change, not new code.
"""
import io

import pandas as pd


def parse_csv_bytes(content: bytes) -> list[dict]:
    """Parses raw CSV bytes into a list of dicts, one per row, keyed by the
    CSV's own original column headers (not yet mapped to our canonical
    field names — that happens in map_row)."""
    df = pd.read_csv(io.BytesIO(content), dtype=str, keep_default_na=False)
    return df.to_dict(orient="records")


def map_row(raw_row: dict, column_map: dict[str, str]) -> dict:
    """Translates a raw CSV row (channel-specific column names) into our
    canonical field names, using the channel's column_map. Missing source
    columns map to None rather than raising — validation happens later,
    per-field, so one missing optional column doesn't kill the whole row."""
    mapped = {}
    for canonical_field, source_column in column_map.items():
        value = raw_row.get(source_column)
        mapped[canonical_field] = value if value not in (None, "") else None
    return mapped

def parse_size(size_text: str) -> int:
    """
    Parse size from strings like '59.81 GB', '1.5 TIB', or '123' (assuming bytes if no unit).
    Supports TIB/TB, GIB/GB, MIB/MB, KIB/KB, B, and no unit.
    Returns 0 on failure.
    """
    import re

    match = re.match(r"([\d.]+)\s*(TIB|TB|GIB|GB|MIB|MB|KIB|KB|B)?", size_text.upper().strip())
    if not match:
        return 0
    value = float(match.group(1))
    unit = match.group(2)
    multipliers = {
        "B": 1,
        "KIB": 1024,
        "KB": 1024,
        "MIB": 1024**2,
        "MB": 1024**2,
        "GIB": 1024**3,
        "GB": 1024**3,
        "TIB": 1024**4,
        "TB": 1024**4,
    }
    multiplier = multipliers.get(unit, 1) if unit else 1
    return int(value * multiplier)

"""What every adaptor shares."""


def at(rows: list[str], index: int) -> str:
    try:
        return rows[index] or ""
    except IndexError:
        return ""

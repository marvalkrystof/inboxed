import re

from inboxed.domain.errors import ModelError


def normalize_tags(names: list[str]) -> list[str]:
    result = []
    seen = set()
    for name in names:
        name = " ".join(name.split())
        if not name or len(name) > 40 or "/" in name or re.search(r"[\x00-\x1f\x7f]", name):
            raise ModelError(
                "Tag names must be 1–40 characters without slashes or control characters"
            )
        key = name.casefold()
        if key not in seen:
            result.append(name)
            seen.add(key)
    if len(result) > 3:
        raise ModelError("At most three tags are allowed")
    return result

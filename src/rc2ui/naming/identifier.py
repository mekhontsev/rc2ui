from __future__ import annotations

import re
import unicodedata


_CYRILLIC = str.maketrans(
    {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e",
        "ё": "yo", "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k",
        "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
        "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "ts",
        "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "",
        "э": "e", "ю": "yu", "я": "ya",
    }
)


def lower_camel_identifier(value: str) -> str | None:
    """Normalize words, snake case and CamelCase to a Qt object-name stem."""

    value = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", value)
    value = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", value)
    value = value.casefold().translate(_CYRILLIC)
    value = unicodedata.normalize("NFKD", value)
    value = "".join(char for char in value if not unicodedata.combining(char))
    words = re.findall(r"[A-Za-z0-9]+", value)
    if not words:
        return None
    result = words[0].lower() + "".join(word.capitalize() for word in words[1:])
    if result[0].isdigit():
        result = "value" + result
    return result

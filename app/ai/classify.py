"""classify_line(description, client) -> Category | None.

Only for a line whose category neither the line nor the keyword table gives. The masked
description alone goes to the model, and the answer must be one value from the closed list.
Anything else (no token, timeout, another answer) gives None; the caller then reports the line
as unclassified. There is no retry: a check must not wait on the model.
"""

from __future__ import annotations

from app.ai.client import ModelClient, ModelError
from app.ai.masking import mask
from app.ai.prompts import line_category_prompt
from app.schemas import CATEGORIES


def classify_line(
    description: str, client: ModelClient, known_names: list[str] | tuple[str, ...] = ()
) -> str | None:
    masked, _ = mask(description, known_names)
    try:
        reply = client.complete_json("line_category", line_category_prompt(masked))
    except ModelError:
        return None
    category = reply.get("category")
    return category if isinstance(category, str) and category in CATEGORIES else None

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class Post:
    id: int
    content_type: str  # text | photo | animation | video
    file_id: Optional[str]
    text: Optional[str]
    link_override: Optional[str]
    created_at: int 
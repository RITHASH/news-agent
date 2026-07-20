from datetime import datetime 
from typing import List, Optional

from pydantic import BaseModel, Field, HttpUrl

class NewsArticle(BaseModel):
    id : str 
    title: str
    summary: Optional[str] = None
    content: Optional[str] = None

    url: HttpUrl

    source: str
    author: Optional[str] = None

    published_at: datetime

    category: Optional[str] = None
    language: str = "en"

    importance_score: int = Field(default=0, ge=0, le=100)

    # LLM-generated enrichment (populated by NewsSummarizer).
    one_line_summary: Optional[str] = None
    why_it_matters: Optional[str] = None
    possible_impact: Optional[str] = None

    tags: List[str] = Field(default_factory=list)

    fetched_at: datetime = Field(default_factory=datetime.utcnow)




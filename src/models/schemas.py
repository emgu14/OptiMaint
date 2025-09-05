from pydantic import BaseModel
from typing import Optional

class ProcessLogQuery(BaseModel):
    language: str = "fr"
    top_k: Optional[int]
    min_count: Optional[int]

class GeminiAnswer(BaseModel):
    solution: str

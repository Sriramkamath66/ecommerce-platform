from pydantic import BaseModel
from typing import Optional, List


class RecommendationResult(BaseModel):
    product_id: str
    score: float
    rank: Optional[int] = None
    reason: Optional[str] = None


class RecommendationResponse(BaseModel):
    recommendations: List[RecommendationResult]
    user_id: Optional[str] = None
    product_id: Optional[str] = None


class TrackEventRequest(BaseModel):
    product_id: str
    event_type: str  # "view" | "click" | "purchase"

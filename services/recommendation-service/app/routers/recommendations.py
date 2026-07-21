import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.auth import get_current_user, get_current_user_id, is_admin
from app.schemas.recommendation import (
    RecommendationResponse,
    RecommendationResult,
    TrackEventRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/recommendations", tags=["recommendations"])


@router.get("/user/{user_id}", response_model=RecommendationResponse)
async def get_user_recommendations(
    user_id: str,
    request: Request,
    limit: int = Query(default=10, ge=1, le=50),
    current_user: dict = Depends(get_current_user),
    current_user_id: str = Depends(get_current_user_id),
) -> RecommendationResponse:
    """
    Return personalised recommendations for *user_id*.

    A regular user may only fetch their own recommendations.
    An admin may fetch recommendations for any user.
    """
    if current_user_id != user_id and not is_admin(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not allowed to view another user's recommendations",
        )

    svc = request.app.state.recommendation_service
    results = await svc.get_user_recommendations(user_id=user_id, limit=limit)
    return RecommendationResponse(recommendations=results, user_id=user_id)


@router.get(
    "/product/{product_id}/similar",
    response_model=RecommendationResponse,
)
async def get_similar_products(
    product_id: str,
    request: Request,
    limit: int = Query(default=10, ge=1, le=50),
) -> RecommendationResponse:
    """
    Return products similar to *product_id* based on embedding similarity.

    This endpoint is public — no authentication required.
    """
    svc = request.app.state.recommendation_service
    results = await svc.get_similar_products(product_id=product_id, limit=limit)
    return RecommendationResponse(recommendations=results, product_id=product_id)


@router.post("/track", status_code=status.HTTP_200_OK)
async def track_event(
    body: TrackEventRequest,
    request: Request,
    current_user_id: str = Depends(get_current_user_id),
) -> dict:
    """
    Record a product interaction event (view / click / purchase) for the
    authenticated user.
    """
    valid_event_types = {"view", "click", "purchase"}
    if body.event_type not in valid_event_types:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"event_type must be one of {sorted(valid_event_types)}",
        )

    svc = request.app.state.recommendation_service
    await svc.track_event(
        user_id=current_user_id,
        product_id=body.product_id,
        event_type=body.event_type,
    )
    return {"status": "ok"}

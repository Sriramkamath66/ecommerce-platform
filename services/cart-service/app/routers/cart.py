from typing import Annotated
import httpx
import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app.dependencies import get_current_user, get_http_client, get_redis
from app.services import cart_service

router = APIRouter(prefix="/cart", tags=["cart"])


class AddItemRequest(BaseModel):
    product_id: str
    quantity: int = Field(gt=0)


class UpdateItemRequest(BaseModel):
    quantity: int = Field(ge=0)


class ApplyCouponRequest(BaseModel):
    coupon_code: str


class ShippingAddress(BaseModel):
    street: str
    city: str
    state: str
    zip: str
    country: str


class CheckoutRequest(BaseModel):
    shipping_address: ShippingAddress


@router.get("/")
async def get_cart(
    current_user: Annotated[dict, Depends(get_current_user)],
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
    http_client: Annotated[httpx.AsyncClient, Depends(get_http_client)],
    settings: Annotated[Settings, Depends(get_settings)],
):
    user_id = str(current_user.get("sub", current_user.get("user_id", "anonymous")))
    return await cart_service.get_cart(redis, user_id, http_client, settings.PRODUCT_SERVICE_URL)


@router.post("/items", status_code=status.HTTP_201_CREATED)
async def add_item(
    body: AddItemRequest,
    current_user: Annotated[dict, Depends(get_current_user)],
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
):
    user_id = str(current_user.get("sub", current_user.get("user_id", "anonymous")))
    try:
        await cart_service.add_item(redis, user_id, body.product_id, body.quantity)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return {"message": "Item added to cart"}


@router.put("/items/{product_id}")
async def update_item(
    product_id: str,
    body: UpdateItemRequest,
    current_user: Annotated[dict, Depends(get_current_user)],
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
):
    user_id = str(current_user.get("sub", current_user.get("user_id", "anonymous")))
    await cart_service.update_item(redis, user_id, product_id, body.quantity)
    return {"message": "Cart updated"}


@router.delete("/items/{product_id}")
async def remove_item(
    product_id: str,
    current_user: Annotated[dict, Depends(get_current_user)],
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
):
    user_id = str(current_user.get("sub", current_user.get("user_id", "anonymous")))
    await cart_service.remove_item(redis, user_id, product_id)
    return {"message": "Item removed from cart"}


@router.delete("/")
async def clear_cart(
    current_user: Annotated[dict, Depends(get_current_user)],
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
):
    user_id = str(current_user.get("sub", current_user.get("user_id", "anonymous")))
    await cart_service.clear_cart(redis, user_id)
    return {"message": "Cart cleared"}


@router.post("/apply-coupon")
async def apply_coupon(
    body: ApplyCouponRequest,
    current_user: Annotated[dict, Depends(get_current_user)],
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
):
    user_id = str(current_user.get("sub", current_user.get("user_id", "anonymous")))
    try:
        result = await cart_service.apply_coupon(redis, user_id, body.coupon_code)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return result


@router.post("/checkout")
async def checkout(
    body: CheckoutRequest,
    current_user: Annotated[dict, Depends(get_current_user)],
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
    http_client: Annotated[httpx.AsyncClient, Depends(get_http_client)],
    settings: Annotated[Settings, Depends(get_settings)],
):
    user_id = str(current_user.get("sub", current_user.get("user_id", "anonymous")))
    try:
        result = await cart_service.checkout(
            redis,
            http_client,
            user_id,
            body.shipping_address.model_dump(),
            settings.PRODUCT_SERVICE_URL,
            settings.ORDER_SERVICE_URL,
            settings.INVENTORY_SERVICE_URL,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return result

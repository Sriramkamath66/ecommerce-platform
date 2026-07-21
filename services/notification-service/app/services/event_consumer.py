import asyncio
import json
import logging

from app.services.notification_service import process_event

logger = logging.getLogger(__name__)

CHANNELS = [
    "payment.completed",
    "payment.failed",
    "order.status_changed",
    "inventory.low_stock",
]


async def start_consumer(app_state) -> None:
    """Long-running background coroutine that consumes Redis pub/sub messages.

    *app_state* is expected to expose the following attributes:

    * ``redis``            – a connected ``redis.asyncio`` client
    * ``db_session_maker`` – an ``async_sessionmaker[AsyncSession]`` factory
    * ``email_service``    – an :class:`~app.services.email_service.EmailService`
    * ``http_client``      – an ``httpx.AsyncClient``

    On any unexpected exception the consumer logs the error, waits five seconds,
    and then reconnects and re-subscribes so that transient failures do not
    permanently halt event processing.  A ``CancelledError`` propagates
    immediately, allowing clean shutdown during application teardown.
    """
    while True:
        pubsub = None
        try:
            redis = app_state.redis
            pubsub = redis.pubsub()
            await pubsub.subscribe(*CHANNELS)
            logger.info("Event consumer subscribed to: %s", CHANNELS)

            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue

                channel = message["channel"]
                if isinstance(channel, bytes):
                    channel = channel.decode("utf-8")

                raw_data = message["data"]
                try:
                    payload: dict = json.loads(raw_data)
                except (json.JSONDecodeError, TypeError) as exc:
                    logger.error(
                        "Failed to parse message on channel %s: %s — data: %r",
                        channel,
                        exc,
                        raw_data,
                    )
                    continue

                async with app_state.db_session_maker() as db:
                    try:
                        await process_event(
                            db,
                            app_state.redis,
                            app_state.email_service,
                            app_state.http_client,
                            channel,
                            payload,
                        )
                        await db.commit()
                    except Exception as exc:
                        logger.error(
                            "Error processing event on channel %s: %s",
                            channel,
                            exc,
                            exc_info=True,
                        )
                        await db.rollback()

        except asyncio.CancelledError:
            logger.info("Event consumer received cancellation — shutting down.")
            if pubsub is not None:
                try:
                    await pubsub.unsubscribe()
                    await pubsub.aclose()
                except Exception:
                    pass
            raise

        except Exception as exc:
            logger.error(
                "Event consumer encountered an unexpected error: %s — "
                "will reconnect in 5 s.",
                exc,
                exc_info=True,
            )
            if pubsub is not None:
                try:
                    await pubsub.aclose()
                except Exception:
                    pass
            await asyncio.sleep(5)

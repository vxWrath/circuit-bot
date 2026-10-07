import asyncio
import signal

from circuit_bot.core import (
    CircuitBot,
    get_env,
    get_logger,
    install_asyncio_exception_handler,
    install_excepthook,
    setup_discord_logger,
)

logger = get_logger("main")

try:
    import uvloop  # type: ignore

    loop_factory = uvloop.new_event_loop

    logger.info("Using uvloop event loop")
except ImportError:
    loop_factory = asyncio.new_event_loop
    logger.info("Using asyncio event loop")


def shutdown(bot: CircuitBot, sig: signal.Signals):
    logger.warning("Received %s, shutting down...", sig.name)

    try:
        bot.loop.create_task(bot.close())
    except RuntimeError as e:
        logger.error("Event loop is already closed, cannot close bot gracefully.", exc_info=e)


async def run():
    install_excepthook()
    install_asyncio_exception_handler()

    token = get_env("DISCORD_TOKEN")
    bot = CircuitBot()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown, bot, sig)

    setup_discord_logger()

    await bot.cache.connect()
    # await bot.database.connect()

    logger.info("Bot started")

    async with bot.initialize():
        try:
            await bot.login(token)
            logger.info("Logged in as %s", bot.user)

            await bot.connect(reconnect=True)
        except Exception as e:
            logger.error("Failed to start bot: %s", exc_info=e)
            raise

    await bot.close()
    await bot.session.close()
    await bot.cache.close()
    # await bot.database.close()

    tasks = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
    for task in tasks:
        task.cancel()

    await asyncio.gather(*tasks, return_exceptions=True)

    logger.info("Bot shutdown complete")


def main() -> None:
    """Sync entry point for ``python -m circuit_bot`` and the ``circuit-bot`` console script."""
    asyncio.run(run(), loop_factory=loop_factory)


if __name__ == "__main__":
    main()

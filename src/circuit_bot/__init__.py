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


def shutdown(bot: CircuitBot, sig: signal.Signals) -> None:
    logger.warning("Received %s, shutting down...", sig.name)

    try:
        bot.loop.create_task(bot.close())
    except RuntimeError as e:
        logger.error("Event loop is already closed, cannot close bot gracefully.", exc_info=e)


async def run() -> None:
    install_excepthook()
    install_asyncio_exception_handler()

    token = get_env("DISCORD_TOKEN")
    bot = CircuitBot()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown, bot, sig)

    setup_discord_logger()

    await bot.cache.connect()
    await bot.database.connect()

    logger.info("Bot started")

    # The bot is its own context manager - discord.py's, which sets the client's
    # loop up on the way in and closes it on the way out, however the block ends.
    async with bot:
        try:
            await bot.login(token)
            logger.info("Logged in as %s", bot.user)

            await bot.connect(reconnect=True)
        except Exception as e:
            logger.error("Failed to start bot: %s", exc_info=e)
            raise

    # Cancel whatever is still running before closing the resources it uses: a
    # task holding a database connection stalls Pool.close() until it lets go,
    # and one cancelled mid-request would fail against a closed session.
    tasks = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
    for task in tasks:
        task.cancel()

    await asyncio.gather(*tasks, return_exceptions=True)

    await bot.session.close()
    await bot.cache.close()
    await bot.database.close()

    logger.info("Bot shutdown complete")


def main() -> None:
    """Sync entry point for the ``circuit-bot`` console script.

    The loop is uvloop's, or there is no run at all: that is the loop signal
    handling is wired up against, and uvloop doesn't support Windows - so a bot
    started somewhere it can't be imported stops here, loudly, rather than
    running with no way to shut it down.
    """

    try:
        # Not installed off the platforms uvloop supports, and the type checker
        # runs wherever the bot is edited.
        import uvloop  # type: ignore
    except ImportError as e:
        raise RuntimeError("circuit-bot runs on uvloop, and this interpreter doesn't have it") from e

    asyncio.run(run(), loop_factory=uvloop.new_event_loop)


if __name__ == "__main__":
    main()

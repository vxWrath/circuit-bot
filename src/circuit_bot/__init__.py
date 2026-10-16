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


def shutdown(task: asyncio.Task[None] | None, sig: signal.Signals) -> None:
    """Cancel run(); the bot's context manager runs bot.close() on the way out."""

    logger.warning("Received %s, shutting down...", sig.name)

    if task is not None:
        task.cancel()


async def run() -> None:
    install_excepthook()
    install_asyncio_exception_handler()

    token = get_env("DISCORD_TOKEN")
    bot = CircuitBot()

    running_task = asyncio.current_task()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown, running_task, sig)

    setup_discord_logger()

    try:
        await bot.cache.connect()
        await bot.database.connect()

        logger.info("Bot started")

        async with bot:
            try:
                await bot.login(token)
                logger.info("Logged in as %s", bot.user)

                await bot.connect(reconnect=True)
            except Exception as e:
                logger.error("Failed to start bot: %s", exc_info=e)
                raise
    except asyncio.CancelledError:
        # Expected when the signal handler cancels the run.
        pass
    finally:
        # Cancel whatever is still running before closing the resources it uses.
        tasks = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
        for task in tasks:
            task.cancel()

        await asyncio.gather(*tasks, return_exceptions=True)

        await bot.session.close()
        await bot.cache.close()
        await bot.database.close()

        logger.info("Bot shutdown complete")


def main() -> None:
    """Sync entry point for the ``circuit-bot`` console script."""

    try:
        # uvloop only
        import uvloop  # type: ignore  # ignoring so windows doesn't complain
    except ImportError as e:
        raise RuntimeError("uvloop must be installed. This machine likely doesn't support it.") from e

    asyncio.run(run(), loop_factory=uvloop.new_event_loop)


if __name__ == "__main__":
    main()

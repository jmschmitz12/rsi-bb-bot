from discord.ext import commands
from config import MY_USER_ID


def is_bot_owner() -> commands.check:
    """
    Command check that restricts a command to the bot owner (MY_USER_ID in .env).

    Usage:
        @commands.command()
        @is_bot_owner()
        async def my_command(self, ctx): ...
    """
    def predicate(ctx: commands.Context) -> bool:
        return ctx.author.id == MY_USER_ID

    return commands.check(predicate)

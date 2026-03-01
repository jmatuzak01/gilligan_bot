import os
import asyncio
import logging
import discord
from discord.ext import commands
from dotenv import load_dotenv
from logging.handlers import RotatingFileHandler

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

#===========================================
# ---------------- LOGGING -----------------
#===========================================

logger = logging.getLogger('gilligan_bot')
logger.setLevel(logging.DEBUG)
formatter = logging.Formatter(
    fmt='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
file_handler = RotatingFileHandler('gilligan.log', maxBytes=5*1024*1024, backupCount=2, encoding='utf-8')
file_handler.setFormatter(formatter)

console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(file_handler)
logger.addHandler(console_handler)

#===========================================
# ---------------- BOT SETUP --------------
#===========================================

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    help_command=None
)

#===========================================
# ------------- ERROR HANDLER -------------
#===========================================

@bot.event
async def on_command_error(ctx, error):
    if hasattr(ctx.command, 'on_error'):
        return  # Skip global error handler if command has local error handler
    if isinstance(error, commands.CommandNotFound):
        invalid_command = ctx.invoked_with
        await ctx.send(f"!{invalid_command} does not exist. Use `!help` to see available commands.")
    elif isinstance(error, commands.BadArgument):
        await ctx.send(f"Invalid argument. Use `!help {ctx.command.name}` for usage info.")
    else:
        logger.error(f"Unhandled error in command '{ctx.command}': {error}")
        await ctx.send(f"An error occurred: {str(error)}")

#===========================================
# ---------------- READY -------------------
#===========================================

@bot.event
async def on_ready():
    if not hasattr(bot, 'synced'):
        bot.synced = True
        await bot.change_presence(status=discord.Status.online)
        logger.info(f"{bot.user} has connected to Discord.")

#===========================================
# ---------------- START -------------------
#===========================================

async def main():
    async with bot:
        await bot.load_extension("cogs.music")
        await bot.start(TOKEN)

asyncio.run(main())

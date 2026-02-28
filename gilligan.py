# bot.py
import os,random,asyncio,discord,yt_dlp,logging,pathlib
from discord.ext import commands
from dotenv import load_dotenv
from logging.handlers import RotatingFileHandler

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

logger = logging.getLogger('gilligan_bot')
logger.setLevel(logging.DEBUG)
formatter = logging.Formatter('&#%(asctime)s - %(levelname)s - %(message)s, datefmt="%Y-%m-%d %H:%M:%S')
file_handler = RotatingFileHandler('gilligan.log', maxBytes=5*1024*1024, backupCount=2,encoding='utf-8')
file_handler.setFormatter(formatter)

console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(file_handler)
logger.addHandler(console_handler)

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

music_queues = {}
now_playing = {}
is_stopping = {}
user_volumes = {}

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    help_command=None  # disable default help 
)

#===========================================
# ---------------- YTDLP ------------------
#===========================================

ytdl_options = {
    'format': 'bestaudio/best',
    'noplaylist': True,
    'quiet': True,
    'extractor_args': {
        'youtube': {
            'js_runtimes': ['node']
        }
    }
    }
ytdl_playlist_options = {
    'format': 'bestaudio/best',
    'noplaylist': False,
    'extract_flat': 'in_playlist',  # get metadata without downloading each entry up front
    'quiet': True,
    'extractor_args': {
        'youtube': {
            'js_runtimes': ['node']
        }
    }
}

ffmpeg_options = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn'
}

ytdl_playlist = yt_dlp.YoutubeDL(ytdl_playlist_options)
ytdl = yt_dlp.YoutubeDL(ytdl_options)

async def get_audio_data(query):
    MAX_PLAYLIST_SIZE = 100
    loop = asyncio.get_running_loop()

    is_playlist = "playlist" in query.lower() or "list=" in query.lower()
    extractor = ytdl_playlist if is_playlist else ytdl
    logger.info(f"Fetching audio data for query: {query}")
    try:
        data = await loop.run_in_executor(
            None,
            lambda: extractor.extract_info(query, download=False)
        )
    except yt_dlp.utils.DownloadError as e:
        logger.error(f"Failed to fetch audio data for query: '{query}': {e}")
        raise ValueError(f"Failed to fetch audio data for query: {e}")

    songs = []
    if 'entries' in data:
        for entry in data['entries'][:MAX_PLAYLIST_SIZE]:
            if entry is None:
                continue
            songs.append({
                "url": entry.get('url') or f"https://www.youtube.com/watch?v={entry['id']}",
                "title": entry.get('title', 'Unknown Title')
            })
    else:
        songs.append({"url": data['url'], "title": data['title']})

    return songs

async def resolve_song_url(url: str) -> str:
    logger.debug(f"Resolving audio URL: {url}")
    loop = asyncio.get_running_loop()
    try:
        data = await loop.run_in_executor(
            None,
            lambda: ytdl.extract_info(url, download=False)
        )
        return data['url']
    except yt_dlp.utils.DownloadError as e:
        logger.error(f"Could not resolve audio URL: '{url}': {e}")
        raise ValueError(f"Could not resolve audio URL: {e}")
    
async def play_next(ctx):
    guild_id = ctx.guild.id
    logger.debug(f"[Guild {guild_id}] play_next called")
    if is_stopping.get(guild_id):
        return
    
    if not ctx.voice_client or not ctx.voice_client.is_connected():
        music_queues.pop(guild_id, None)
        now_playing.pop(guild_id, None)
        return

    while True:
        if guild_id not in music_queues or len(music_queues[guild_id]) == 0:
            now_playing.pop(guild_id, None)
            if ctx.voice_client:
                await ctx.voice_client.disconnect()
            return

        next_song = music_queues[guild_id].pop(0)
        now_playing[guild_id] = next_song

        try:
            audio_url = await resolve_song_url(next_song["url"])
            break  # successfully resolved, exit the loop
        except ValueError as e:
            logger.warning(f"[Guild {guild_id}] Skipping '{next_song['title']}': {e}")
            await ctx.send(f"Skipping **{next_song['title']}**: {e}")
            continue  # try the next song

    volume = user_volumes.get(ctx.author.id, 0.5)

    source = discord.PCMVolumeTransformer(
        discord.FFmpegPCMAudio(audio_url, **ffmpeg_options),
        volume=volume
    )
    #STOPED LOGGING FOR NOW
    #^^^^^^^^^^^^^^^^^^^^^^
    logger.info(f"[Guild {guild_id}] Now playing: {next_song['title']} (requested by {ctx.author})")
    ctx.voice_client.play(
        source,
        after=lambda e: (
            print(f"Player error: {e}") if e else None,
            asyncio.run_coroutine_threadsafe(play_next(ctx), bot.loop)
        )
    )
    await ctx.send(f"Now Playing: **{next_song['title']}**")

#===========================================
# ---------------- COMMANDS ----------------
#===========================================
#=========
# HELP
#=========
@bot.command(name="help")
async def help_command(ctx, command_name: str = None):

    # If user wants help for a specific command
    if command_name:
        command = bot.get_command(command_name)

        if command is None:
            await ctx.send("That command does not exist.")
            return

        embed = discord.Embed(
            title=f"Help: !{command.name}",
            color=discord.Color.green()
        )

        embed.add_field(
            name="Description",
            value=command.help or "No description provided.",
            inline=False
        )

        usage = f"!{command.name} {command.signature}"
        embed.add_field(
            name="Usage",
            value=f"`{usage}`",
            inline=False
        )

        await ctx.send(embed=embed)
        return

    # Otherwise show all commands
    embed = discord.Embed(
        title="Gilligan's Commands",
        description="Use `!help <command>` for detailed information.",
        color=discord.Color.blurple()
    )

    for command in bot.commands:
        if command.hidden:
            continue
        if command.name == "help": #skip the help command itself
            continue
        embed.add_field(
            name=f"!{command.name}",
            value=command.help or "No description provided.",
            inline=False
        )

    await ctx.send(embed=embed)
#=========
# PLAY
#=========
@bot.command(help="Add a song or playlist to the queue.\n Example: `!play <Youtube song/playlist URL>`")
async def play(ctx, *, query: str):
    if not ctx.author.voice:
        await ctx.send("You must be in a voice channel.")
        return

    channel = ctx.author.voice.channel

    if ctx.voice_client is None:
        await channel.connect()
    else:
        await ctx.voice_client.move_to(channel)

    guild_id = ctx.guild.id

    if guild_id not in music_queues:
        music_queues[guild_id] = []

    await ctx.send(f"Searching for: {query}")

    try:
        songs = await get_audio_data(query)
    except ValueError as e:
        await ctx.send(f"Error: {e}")
        return

    if len(songs) > 1:
        await ctx.send(f"Added playlist with {len(songs)} songs to the queue.")
    else:
        await ctx.send(f"Added to queue: **{songs[0]['title']}**")

    music_queues[guild_id].extend(songs)

    if not ctx.voice_client.is_playing():
        await play_next(ctx)

#=========
# NOW PLAYING
#=========
@bot.command(name="np",help="Show the currently playing song.")
async def now_playing_song(ctx):
    guild_id = ctx.guild.id

    if guild_id not in now_playing:
        await ctx.send("No song is currently playing.")
        return
    song = now_playing[guild_id]
    await ctx.send(f"Currently playing: **{song['title']}**")

#=========
# SKIP
#=========
@bot.command(help="Play the next song in the queue")
async def skip(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.stop()  # triggers the `after` callback, which calls play_next
    else:
        await play_next(ctx)
#=========
# QUEUE
#=========
@bot.command(help="Show current music queue with song index.")
async def queue(ctx):
    guild_id = ctx.guild.id

    if guild_id not in music_queues or len(music_queues[guild_id]) == 0:
        await ctx.send("The queue is empty.")
        return

    songs = music_queues[guild_id]
    chunks = [songs[i:i+25] for i in range(0, len(songs), 25)]

    for page, chunk in enumerate(chunks):
        embed = discord.Embed(
            title=f"Music Queue (Page {page + 1}/{len(chunks)})",
            color=discord.Color.blurple()
        )

        for index, song in enumerate(chunk):
            global_index = page * 25 + index + 1
            embed.add_field(
                name=f"{global_index}.",
                value=song["title"],
                inline=False
            )

        await ctx.send(embed=embed)

#=========
# CLEAR QUEUE
#=========
@bot.command(name="clear", help="Clear the current music queue.")
async def clear_queue(ctx):

    guild_id = ctx.guild.id

    if guild_id not in music_queues or len(music_queues[guild_id]) == 0:
        await ctx.send("The queue is already empty.")
        return

    music_queues[guild_id].clear()
    await ctx.send("The queue has been cleared.")

#=========
# REMOVE SONG
#=========
@bot.command(help="Remove a song from the queue by index.\n Example: `!remove 2` removes the second song in the queue.")
async def remove(ctx, index: int):

    guild_id = ctx.guild.id

    if guild_id not in music_queues or len(music_queues[guild_id]) == 0:
        await ctx.send("The queue is empty.")
        return

    if index < 1 or index > len(music_queues[guild_id]):
        await ctx.send("Invalid index.")
        return

    removed_song = music_queues[guild_id].pop(index - 1)

    await ctx.send(f"Removed: **{removed_song['title']}**")
#=========
# PAUSE SONG
#=========
@bot.command(help="Pause the current audio.")
async def pause(ctx):
    """Pause audio"""

    vc = ctx.voice_client

    if vc and vc.is_playing():
        vc.pause()
        await ctx.send("Paused.")
    else:
        await ctx.send("Nothing is playing.")

#=========
# RESUME SONG
#=========
@bot.command(help="Resume paused audio.")
async def resume(ctx):
    """Resume audio"""

    vc = ctx.voice_client

    if vc and vc.is_paused():
        vc.resume()
        await ctx.send("Resumed.")
    else:
        await ctx.send("Nothing is paused.")

#=========
# STOP SONG
#=========
@bot.command(help="Stop playback and disconnect from voice.")
async def stop(ctx):
    guild_id = ctx.guild.id
    vc = ctx.voice_client

    if vc:
        is_stopping[guild_id] = True
        music_queues.pop(guild_id, None)
        now_playing.pop(guild_id, None)
        if vc.is_playing() or vc.is_paused():
            vc.stop()
        await vc.disconnect()
        is_stopping.pop(guild_id, None)
        await ctx.send("Disconnected.")
    else:
        await ctx.send("Not in a voice channel.")

#=========
# VOLUME
#=========
@bot.command(help="Set playback volume (0-100). \n Example: `!volume 75` sets volume to 75%")
async def volume(ctx, volume: int = None):
    vc = ctx.voice_client

    if not vc or not vc.is_playing() or vc.is_paused():
        await ctx.send("Nothing is playing.")
        return
    
    if volume is None:
        current = int(vc.source.volume * 100)
        await ctx.send(f"Current volume: {current}%")
        return

    if volume < 0 or volume > 100:
        await ctx.send("Volume must be between 0 and 100.")
        return
    
    user_volumes[ctx.author.id] = volume / 100
    vc.source.volume = volume / 100
    await ctx.send(f"Volume set to {volume}%")

#=========
# SHUFFLE
#=========
@bot.command(help="Shuffle the current music queue.")
async def shuffle(ctx):

    guild_id = ctx.guild.id

    if guild_id not in music_queues or len(music_queues[guild_id]) < 2:
        await ctx.send("The queue is empty or has only one song.")
        return

    random.shuffle(music_queues[guild_id])
    await ctx.send("The queue has been shuffled.")

# @bot.command(help="Ask and you shall recieve")
# async def cat_boy(ctx):
#     valid_extensions = {".jpg", ".jpeg", ".png", ".gif", ".webp",".gif"}
#     all_files = [
#         f for f in image_dir.rglob("*")
#         if f.is_file() and f.suffix.lower() in valid_extensions
#     ]
#     if not all_files:
#         await ctx.send("No images found.")
#         return
#     random_file = random.choice(all_files)
#     await ctx.send(file=discord.File(random_file))
#===========================================
# ------------- ERROR HANDLERS -------------
#===========================================
@play.error
async def play_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("Please provide a song name or URL.")
    else:
        await ctx.send(f"An error occurred: {str(error)}")
@remove.error
async def remove_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("Please provide the index of the song to remove. Example: `!remove 2`")
    elif isinstance(error, commands.BadArgument):
        await ctx.send("Index must be a number. Example: `!remove 2`")
    else:
        await ctx.send(f"An error occurred: {str(error)}")

@bot.event
async def on_command_error(ctx, error):
    if hasattr(ctx.command, 'on_error'):
        return # Skip global error handler if command has local error handler
    if isinstance(error, commands.CommandNotFound):
        invalid_command = ctx.invoked_with
        await ctx.send(f"!{invalid_command} does not exist. Use `!help` to see available commands.")
    elif isinstance(error, commands.BadArgument):
        await ctx.send(f"Invalid argument. Use `!help {ctx.command.name}` for usage info.")
    else:
        await ctx.send(f"An error occurred: {str(error)}")
#===========================================
# ---------------- READY -------------------
#===========================================

@bot.event
async def on_ready():
    if not hasattr(bot, 'synced'):
        bot.synced = True
        print(f"{bot.user} is ready!")

bot.run(TOKEN)
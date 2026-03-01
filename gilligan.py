# bot.py
import os,random,asyncio,discord,yt_dlp,logging,pathlib
from discord.ext import commands
from dotenv import load_dotenv
from logging.handlers import RotatingFileHandler

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

logger = logging.getLogger('gilligan_bot')
logger.setLevel(logging.DEBUG)
formatter = logging.Formatter(
    fmt='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
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
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -timeout 10000000',
    'options': '-vn -bufsize 512k'
}

ytdl_playlist = yt_dlp.YoutubeDL(ytdl_playlist_options)
ytdl = yt_dlp.YoutubeDL(ytdl_options)

#===========================================
# ---------------- HELPERS -----------------
#===========================================

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
            logger.error(f"[Guild {guild_id}] Player error: {e}") if e else None,
            asyncio.run_coroutine_threadsafe(play_next(ctx), bot.loop)
        )
    )
    await ctx.send(f"Now Playing: **{next_song['title']}**")

def format_duration(seconds: int) -> str:
    if not seconds:
        return "Unknown"
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02}:{secs:02}"
    return f"{minutes}:{secs:02}"

#===========================================
# ---------------- BASE VIEW ---------------
#===========================================
class PagedView(discord.ui.View):
    """Base class for paginated views."""
    def __init__(self, ctx, items, page_size, timeout=60):
        super().__init__(timeout=timeout)
        self.ctx = ctx
        self.items = items
        self.page = 0
        self.page_size = page_size
        self.total_pages = (len(items) + page_size - 1) // page_size
        self.message = None
        self.update_buttons()

    def update_buttons(self):
        self.clear_items()
        self.add_page_buttons()

    def add_page_buttons(self):
        prev_button = discord.ui.Button(
            label="◀ Previous",
            style=discord.ButtonStyle.blurple,
            disabled=self.page == 0,
            custom_id="prev"
        )
        prev_button.callback = self.prev_page
        self.add_item(prev_button)

        page_button = discord.ui.Button(
            label=f"Page {self.page + 1}/{self.total_pages}",
            style=discord.ButtonStyle.grey,
            disabled=True,
            custom_id="page_indicator"
        )
        self.add_item(page_button)

        next_button = discord.ui.Button(
            label="Next ▶",
            style=discord.ButtonStyle.blurple,
            disabled=self.page >= self.total_pages - 1,
            custom_id="next"
        )
        next_button.callback = self.next_page
        self.add_item(next_button)

    def build_embed(self):
        raise NotImplementedError("Subclasses must implement build_embed()")

    async def prev_page(self, interaction: discord.Interaction):
        if interaction.user != self.ctx.author:
            await interaction.response.send_message(
                "Only the original user can change pages.", ephemeral=True
            )
            return
        self.page -= 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def next_page(self, interaction: discord.Interaction):
        if interaction.user != self.ctx.author:
            await interaction.response.send_message(
                "Only the original user can change pages.", ephemeral=True
            )
            return
        self.page += 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        try:
            await self.message.edit(view=self)
        except discord.NotFound:
            pass

#=========
# SEARCH VIEW
#=========
class SearchView(PagedView):
    def __init__(self, ctx, results):
        super().__init__(ctx, results, page_size=5)

    def update_buttons(self):
        self.clear_items()

        start = self.page * self.page_size
        end = min(start + self.page_size, len(self.items))

        for i in range(end - start):
            button = discord.ui.Button(
                label=f"Add #{start + i + 1}",
                style=discord.ButtonStyle.green,
                custom_id=f"add_{start + i}"
            )
            button.callback = self.make_add_callback(start + i)
            self.add_item(button)

        self.add_page_buttons()

    def make_add_callback(self, index):
        async def callback(interaction: discord.Interaction):
            if interaction.user != self.ctx.author:
                await interaction.response.send_message(
                    "Only the person who searched can add songs.", ephemeral=True
                )
                return
            if not interaction.user.voice:
                await interaction.response.send_message(
                    "You must be in a voice channel.", ephemeral=True
                )
                return

            song = self.items[index]
            guild_id = self.ctx.guild.id

            if guild_id not in music_queues:
                music_queues[guild_id] = []

            if self.ctx.voice_client is None:
                await interaction.user.voice.channel.connect()
            elif self.ctx.voice_client.channel != interaction.user.voice.channel:
                await self.ctx.voice_client.move_to(interaction.user.voice.channel)

            music_queues[guild_id].append(song)
            await interaction.response.send_message(f"Added to queue: **{song['title']}**")

            if not self.ctx.voice_client.is_playing():
                await play_next(self.ctx)

        return callback

    def build_embed(self):
        start = self.page * self.page_size
        end = min(start + self.page_size, len(self.items))

        embed = discord.Embed(
            title="🔎 Search Results",
            description=f"Showing results {start + 1}–{end} of {len(self.items)}",
            color=discord.Color.blurple()
        )
        for i, song in enumerate(self.items[start:end]):
            duration = format_duration(song.get("duration", 0))
            embed.add_field(
                name=f"{start + i + 1}. {song['title']}",
                value=f"By {song.get('uploader', 'Unknown')} | Duration: {duration}",
                inline=False
            )
        embed.set_footer(text="Buttons expire after 60 seconds.")
        return embed

#=========
# QUEUE VIEW
#=========
class QueueView(PagedView):
    def __init__(self, ctx, songs):
        super().__init__(ctx, songs, page_size=25)

    def build_embed(self):
        start = self.page * self.page_size
        end = min(start + self.page_size, len(self.items))

        embed = discord.Embed(
            title="🎵 Music Queue",
            description=f"Showing songs {start + 1}–{end} of {len(self.items)}",
            color=discord.Color.blurple()
        )
        for i, song in enumerate(self.items[start:end]):
            embed.add_field(
                name=f"{start + i + 1}.",
                value=song["title"],
                inline=False
            )
        embed.set_footer(text="Buttons expire after 60 seconds.")
        return embed
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

    view = QueueView(ctx, music_queues[guild_id])
    message = await ctx.send(embed=view.build_embed(), view=view)
    view.message = message

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

    if not vc or not (vc.is_playing() or vc.is_paused()):
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

#=========
# SEARCH
#=========
@bot.command(help="Search YouTube for a song and add it to the queue.\n Example: `!search never gonna give you up`")
async def search(ctx, *, query: str = None):
    if query is None:
        await ctx.send("Please provide a search query.")
        return
    if not ctx.author.voice:
        await ctx.send("You must be in a voice channel.")
        return
    await ctx.send(f"Searching for: {query}")
    loop = asyncio.get_running_loop()
    try:
        data = await loop.run_in_executor(
            None,
            lambda: yt_dlp.YoutubeDL({
                'quiet': True,
                'extract_flat': True,
                'default_search': 'ytsearch15',  # fetch 15 results for pagination
                'extractor_args': {'youtube': {'js_runtimes': ['node']}}
            }).extract_info(f"ytsearch15:{query}", download=False)
        )
    except yt_dlp.utils.DownloadError as e:
        logger.error(f"Error during search for query '{query}': {e}")
        await ctx.send(f"Error during search: {e}")
        return
    results = []
    for entry in data.get('entries', []):
        if entry is None:
            continue
        results.append({
            "url": entry.get('url') or f"https://www.youtube.com/watch?v={entry['id']}",
            "title": entry.get('title', 'Unknown Title'),
            "uploader": entry.get('uploader', 'Unknown Uploader'),
            "duration": entry.get('duration', 0)
        })
    if not results:
        logger.warning(f"No search results found for query: '{query}'")
        await ctx.send("No results found.")
        return
    view = SearchView(ctx, results)
    message = await ctx.send(embed=view.build_embed(), view=view)
    view.message = message

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
        logger.error(f"Unhandled error in command '{ctx.command}': {error}")
        await ctx.send(f"An error occurred: {str(error)}")

@bot.event
async def on_voice_state_update(member, before, after):
    if member.id != bot.user.id:
        return
    # If the bot was disconnected from a voice channel, clear the queue and now playing
    if before.channel is not None and after.channel is None:
        guild_id = before.channel.guild.id
        logger.warning(f"Bot was disconnected from voice channel in guild {guild_id}. Clearing queue and now playing.")
        music_queues.pop(guild_id, None)
        now_playing.pop(guild_id, None)
        is_stopping.pop(guild_id, None)
#===========================================
# ---------------- READY -------------------
#===========================================

@bot.event
async def on_ready():
    if not hasattr(bot, 'synced'):
        bot.synced = True
        await bot.change_presence(status=discord.Status.online)
        logger.info(f"{bot.user} has connected to Discord.")

bot.run(TOKEN)
import random
import logging
import discord
import yt_dlp
from discord.ext import commands
from state import music_queues, now_playing, is_stopping, user_volumes
from audio import get_audio_data, play_next, search_youtube
from views import SearchView, QueueView

logger = logging.getLogger('gilligan_bot')

class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    #=========
    # HELP
    #=========
    @commands.command(name="help")
    async def help_command(self, ctx, command_name: str = None):

        # If user wants help for a specific command
        if command_name:
            command = self.bot.get_command(command_name)

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

        for command in self.bot.commands:
            if command.hidden:
                continue
            if command.name == "help":
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
    @commands.command(help="Add a song or playlist to the queue.\n Example: `!play <Youtube song/playlist URL>`")
    async def play(self, ctx, *, query: str):
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
    @commands.command(name="np", help="Show the currently playing song.")
    async def now_playing_song(self, ctx):
        guild_id = ctx.guild.id

        if guild_id not in now_playing:
            await ctx.send("No song is currently playing.")
            return
        song = now_playing[guild_id]
        await ctx.send(f"Currently playing: **{song['title']}**")

    #=========
    # SKIP
    #=========
    @commands.command(help="Play the next song in the queue")
    async def skip(self, ctx):
        if ctx.voice_client and ctx.voice_client.is_playing():
            ctx.voice_client.stop()  # triggers the `after` callback, which calls play_next
        else:
            await play_next(ctx)

    #=========
    # QUEUE
    #=========
    @commands.command(help="Show current music queue with song index.")
    async def queue(self, ctx):
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
    @commands.command(name="clear", help="Clear the current music queue.")
    async def clear_queue(self, ctx):

        guild_id = ctx.guild.id

        if guild_id not in music_queues or len(music_queues[guild_id]) == 0:
            await ctx.send("The queue is already empty.")
            return

        music_queues[guild_id].clear()
        await ctx.send("The queue has been cleared.")

    #=========
    # REMOVE SONG
    #=========
    @commands.command(help="Remove a song from the queue by index.\n Example: `!remove 2` removes the second song in the queue.")
    async def remove(self, ctx, index: int):

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
    @commands.command(help="Pause the current audio.")
    async def pause(self, ctx):

        vc = ctx.voice_client

        if vc and vc.is_playing():
            vc.pause()
            await ctx.send("Paused.")
        else:
            await ctx.send("Nothing is playing.")

    #=========
    # RESUME SONG
    #=========
    @commands.command(help="Resume paused audio.")
    async def resume(self, ctx):

        vc = ctx.voice_client

        if vc and vc.is_paused():
            vc.resume()
            await ctx.send("Resumed.")
        else:
            await ctx.send("Nothing is paused.")

    #=========
    # STOP SONG
    #=========
    @commands.command(help="Stop playback and disconnect from voice.")
    async def stop(self, ctx):
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
    @commands.command(help="Set playback volume (0-100). \n Example: `!volume 75` sets volume to 75%")
    async def volume(self, ctx, volume: int = None):
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
    @commands.command(help="Shuffle the current music queue.")
    async def shuffle(self, ctx):

        guild_id = ctx.guild.id

        if guild_id not in music_queues or len(music_queues[guild_id]) < 2:
            await ctx.send("The queue is empty or has only one song.")
            return

        random.shuffle(music_queues[guild_id])
        await ctx.send("The queue has been shuffled.")

    #=========
    # SEARCH
    #=========
    @commands.command(help="Search YouTube for a song and add it to the queue.\n Example: `!search never gonna give you up`")
    async def search(self, ctx, *, query: str = None):
        if query is None:
            await ctx.send("Please provide a search query.")
            return
        if not ctx.author.voice:
            await ctx.send("You must be in a voice channel.")
            return
        await ctx.send(f"Searching for: {query}")
        try:
            results = await search_youtube(query)
        except yt_dlp.utils.DownloadError as e:
            logger.error(f"Error during search for query '{query}': {e}")
            await ctx.send(f"Error during search: {e}")
            return
        if not results:
            logger.warning(f"No search results found for query: '{query}'")
            await ctx.send("No results found.")
            return
        view = SearchView(ctx, results)
        message = await ctx.send(embed=view.build_embed(), view=view)
        view.message = message

    #===========================================
    # ------------- ERROR HANDLERS -------------
    #===========================================
    @play.error
    async def play_error(self, ctx, error):
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send("Please provide a song name or URL.")
        else:
            await ctx.send(f"An error occurred: {str(error)}")

    @remove.error
    async def remove_error(self, ctx, error):
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send("Please provide the index of the song to remove. Example: `!remove 2`")
        elif isinstance(error, commands.BadArgument):
            await ctx.send("Index must be a number. Example: `!remove 2`")
        else:
            await ctx.send(f"An error occurred: {str(error)}")

async def setup(bot):
    await bot.add_cog(Music(bot))

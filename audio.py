import asyncio
import logging
import discord
import yt_dlp
from state import music_queues, now_playing, is_stopping, user_volumes

logger = logging.getLogger('gilligan_bot')

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
    'extract_flat': 'in_playlist',
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
            break
        except ValueError as e:
            logger.warning(f"[Guild {guild_id}] Skipping '{next_song['title']}': {e}")
            await ctx.send(f"Skipping **{next_song['title']}**: {e}")
            continue

    volume = user_volumes.get(ctx.author.id, 0.5)

    source = discord.PCMVolumeTransformer(
        discord.FFmpegPCMAudio(audio_url, **ffmpeg_options),
        volume=volume
    )
    logger.info(f"[Guild {guild_id}] Now playing: {next_song['title']} (requested by {ctx.author})")
    ctx.voice_client.play(
        source,
        after=lambda e: (
            logger.error(f"[Guild {guild_id}] Player error: {e}") if e else None,
            asyncio.run_coroutine_threadsafe(play_next(ctx), ctx.bot.loop)
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

async def search_youtube(query: str, max_results: int = 15):
    loop = asyncio.get_running_loop()
    data = await loop.run_in_executor(
        None,
        lambda: yt_dlp.YoutubeDL({
            'quiet': True,
            'extract_flat': True,
            'default_search': f'ytsearch{max_results}',
            'extractor_args': {'youtube': {'js_runtimes': ['node']}}
        }).extract_info(f"ytsearch{max_results}:{query}", download=False)
    )
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
    return results

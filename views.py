import discord
from state import music_queues
from audio import format_duration, play_next

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
            label="\u25c0 Previous",
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
            label="Next \u25b6",
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

#===========================================
# -------------- SEARCH VIEW ---------------
#===========================================

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
            title="\U0001f50e Search Results",
            description=f"Showing results {start + 1}\u2013{end} of {len(self.items)}",
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

#===========================================
# --------------- QUEUE VIEW ---------------
#===========================================

class QueueView(PagedView):
    def __init__(self, ctx, songs):
        super().__init__(ctx, songs, page_size=25)

    def build_embed(self):
        start = self.page * self.page_size
        end = min(start + self.page_size, len(self.items))

        embed = discord.Embed(
            title="\U0001f3b5 Music Queue",
            description=f"Showing songs {start + 1}\u2013{end} of {len(self.items)}",
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

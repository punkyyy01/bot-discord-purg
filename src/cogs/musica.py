"""Música: reproducción vía yt-dlp con cola, loop y controles con botones."""

import random

import discord
from discord import app_commands
from discord.ext import commands

from music_player import (
    EMBED_COLOR, LoopMode, SongInfo, fmt_duration,
    fetch_song, get_player, remove_player, MediaFetchError, YouTubeNotAllowed,
)


def _voice_check(interaction: discord.Interaction) -> tuple[discord.VoiceChannel | None, str | None]:
    """Returns (voice_channel, error_msg). error_msg is None on success."""
    if not interaction.guild:
        return None, "Solo en servidores."

    member = interaction.user
    if not isinstance(member, discord.Member):
        return None, "No puedo verificar tu canal de voz."

    if not member.voice or not member.voice.channel:
        return None, "Debes estar en un canal de voz."

    vc = member.voice.channel
    if not isinstance(vc, discord.VoiceChannel):
        return None, "Debes estar en un canal de voz normal (no stage)."

    perms = vc.permissions_for(interaction.guild.me)
    if not perms.connect:
        return None, f"No tengo permiso para conectarme a **{vc.name}**."
    if not perms.speak:
        return None, f"No tengo permiso para hablar en **{vc.name}**."

    player = get_player(interaction.guild.id)
    if player.voice_client and player.voice_client.is_connected():
        if player.voice_client.channel.id != vc.id:
            return None, f"Ya estoy en {player.voice_client.channel.mention}. Únete a ese canal."

    return vc, None


class NowPlayingView(discord.ui.View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=120)
        self.guild_id = guild_id

    @discord.ui.button(emoji="⏸️", style=discord.ButtonStyle.secondary, custom_id="np_pause")
    async def pause_resume(self, interaction: discord.Interaction, button: discord.ui.Button):
        player = get_player(self.guild_id)
        if not player.voice_client:
            await interaction.response.send_message("❌ No hay nada reproduciéndose.", ephemeral=True)
            return
        if player.voice_client.is_paused():
            player.voice_client.resume()
            button.emoji = "⏸️"
        elif player.voice_client.is_playing():
            player.voice_client.pause()
            button.emoji = "▶️"
        else:
            await interaction.response.send_message("❌ No hay nada reproduciéndose.", ephemeral=True)
            return
        await interaction.response.edit_message(view=self)

    @discord.ui.button(emoji="⏭️", style=discord.ButtonStyle.secondary, custom_id="np_skip")
    async def skip_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        player = get_player(self.guild_id)
        if not player.voice_client or not player.is_active():
            await interaction.response.send_message("❌ No hay nada reproduciéndose.", ephemeral=True)
            return
        player.voice_client.stop()
        await interaction.response.send_message("⏭️ Canción saltada.", ephemeral=True)

    @discord.ui.button(emoji="🔁", style=discord.ButtonStyle.secondary, custom_id="np_loop")
    async def loop_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        player = get_player(self.guild_id)
        player.loop_mode = player.loop_mode.next()
        await interaction.response.send_message(f"🔁 {player.loop_mode.label()}", ephemeral=True)

    @discord.ui.button(emoji="🔉", style=discord.ButtonStyle.secondary, custom_id="np_vol_down")
    async def vol_down(self, interaction: discord.Interaction, button: discord.ui.Button):
        player = get_player(self.guild_id)
        player.volume = max(0.0, round(player.volume - 0.1, 2))
        if player.voice_client and player.voice_client.source:
            player.voice_client.source.volume = player.volume
        await interaction.response.send_message(
            f"🔉 Volumen: **{int(player.volume * 100)}%**", ephemeral=True
        )

    @discord.ui.button(emoji="🔊", style=discord.ButtonStyle.secondary, custom_id="np_vol_up")
    async def vol_up(self, interaction: discord.Interaction, button: discord.ui.Button):
        player = get_player(self.guild_id)
        player.volume = min(1.0, round(player.volume + 0.1, 2))
        if player.voice_client and player.voice_client.source:
            player.voice_client.source.volume = player.volume
        await interaction.response.send_message(
            f"🔊 Volumen: **{int(player.volume * 100)}%**", ephemeral=True
        )


class QueueView(discord.ui.View):
    PER_PAGE = 10

    def __init__(self, songs: list[SongInfo], current: SongInfo | None, page: int = 0):
        super().__init__(timeout=60)
        self.songs = songs
        self.current = current
        self.page = page
        self._sync_buttons()

    def _max_page(self) -> int:
        return max(0, (len(self.songs) - 1) // self.PER_PAGE) if self.songs else 0

    def _sync_buttons(self):
        for child in self.children:
            if not isinstance(child, discord.ui.Button):
                continue
            label = child.label or ""
            if "Anterior" in label:
                child.disabled = self.page <= 0
            elif "Siguiente" in label:
                child.disabled = self.page >= self._max_page()

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(title="🎵 Cola de reproducción", color=EMBED_COLOR)
        if self.current:
            embed.add_field(
                name="🎶 Reproduciendo ahora",
                value=f"**{self.current.title}** ({fmt_duration(self.current.duration)})",
                inline=False,
            )
        start = self.page * self.PER_PAGE
        page_songs = self.songs[start:start + self.PER_PAGE]
        if page_songs:
            lines = [
                f"`{start + i + 1}.` {s.title} · {fmt_duration(s.duration)}"
                + (f" · {s.requester.display_name}" if s.requester else "")
                for i, s in enumerate(page_songs)
            ]
            embed.add_field(
                name=f"Próximas ({len(self.songs)} en cola)",
                value="\n".join(lines),
                inline=False,
            )
        else:
            embed.add_field(name="Cola", value="Vacía", inline=False)

        total = sum(s.duration for s in self.songs)
        embed.set_footer(
            text=f"Página {self.page + 1}/{self._max_page() + 1} · Total: {fmt_duration(total)}"
        )
        return embed

    @discord.ui.button(label="◀ Anterior", style=discord.ButtonStyle.secondary)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(0, self.page - 1)
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="Siguiente ▶", style=discord.ButtonStyle.secondary)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = min(self._max_page(), self.page + 1)
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)


class Musica(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if not self.bot.user or member.id != self.bot.user.id:
            return
        if before.channel and not after.channel:
            player = get_player(member.guild.id)
            player.voice_client = None
            player.queue.clear()
            player.current = None
            remove_player(member.guild.id)

    @app_commands.command(name="play", description="Reproduce una canción o URL. Si ya hay una, la agrega a la cola.")
    @app_commands.describe(query="Nombre de la canción, artista, o URL de YouTube/SoundCloud")
    async def play(self, interaction: discord.Interaction, query: str):
        vc, err = _voice_check(interaction)
        if err:
            await interaction.response.send_message(
                embed=discord.Embed(description=f"❌ {err}", color=EMBED_COLOR),
                ephemeral=True,
            )
            return

        await interaction.response.defer(thinking=True)

        try:
            song = await fetch_song(query)
        except YouTubeNotAllowed as e:
            await interaction.followup.send(
                embed=discord.Embed(description=f"❌ {e}", color=EMBED_COLOR)
            )
            return
        except MediaFetchError as e:
            await interaction.followup.send(
                embed=discord.Embed(description=f"❌ {e.user_message}", color=EMBED_COLOR)
            )
            return

        if not song:
            await interaction.followup.send(
                embed=discord.Embed(
                    description="❌ No se encontró ninguna canción para esa búsqueda.",
                    color=EMBED_COLOR,
                )
            )
            return

        song.requester = interaction.user  # type: ignore[assignment]
        player = get_player(interaction.guild.id)
        started = await player.play(vc, song, interaction.channel)

        if started:
            embed = discord.Embed(
                title="🎵 Reproduciendo ahora",
                description=f"**[{song.title}]({song.webpage_url})**",
                color=EMBED_COLOR,
            )
            embed.add_field(name="Duración", value=fmt_duration(song.duration), inline=True)
            embed.add_field(name="Volumen", value=f"{int(player.volume * 100)}%", inline=True)
            embed.set_footer(
                text=f"Pedido por {interaction.user.display_name}",
                icon_url=interaction.user.display_avatar.url,
            )
            if song.thumbnail:
                embed.set_thumbnail(url=song.thumbnail)
            await interaction.followup.send(
                embed=embed, view=NowPlayingView(interaction.guild.id)
            )
        else:
            pos = len(player.queue)
            embed = discord.Embed(
                title="📋 Agregado a la cola",
                description=f"**[{song.title}]({song.webpage_url})**",
                color=EMBED_COLOR,
            )
            embed.add_field(name="Posición en cola", value=f"#{pos}", inline=True)
            embed.add_field(name="Duración", value=fmt_duration(song.duration), inline=True)
            embed.set_footer(
                text=f"Pedido por {interaction.user.display_name}",
                icon_url=interaction.user.display_avatar.url,
            )
            if song.thumbnail:
                embed.set_thumbnail(url=song.thumbnail)
            await interaction.followup.send(embed=embed)

    @app_commands.command(name="skip", description="Salta a la siguiente canción en la cola.")
    async def skip(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("Solo en servidores.", ephemeral=True)
            return
        player = get_player(interaction.guild.id)
        if not player.is_active():
            await interaction.response.send_message(
                embed=discord.Embed(description="❌ No hay nada reproduciéndose.", color=EMBED_COLOR),
                ephemeral=True,
            )
            return
        player.voice_client.stop()
        await interaction.response.send_message(
            embed=discord.Embed(description="⏭️ Canción saltada.", color=EMBED_COLOR)
        )

    @app_commands.command(name="stop", description="Detiene la reproducción, vacía la cola y el bot sale del canal.")
    async def stop(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("Solo en servidores.", ephemeral=True)
            return
        player = get_player(interaction.guild.id)
        if not player.voice_client:
            await interaction.response.send_message(
                embed=discord.Embed(description="❌ El bot no está en ningún canal de voz.", color=EMBED_COLOR),
                ephemeral=True,
            )
            return
        await player.cleanup()
        remove_player(interaction.guild.id)
        await interaction.response.send_message(
            embed=discord.Embed(description="⏹️ Reproducción detenida y cola vaciada.", color=EMBED_COLOR)
        )

    @app_commands.command(name="pause", description="Pausa la reproducción actual.")
    async def pause(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("Solo en servidores.", ephemeral=True)
            return
        player = get_player(interaction.guild.id)
        if not player.voice_client or not player.voice_client.is_playing():
            await interaction.response.send_message(
                embed=discord.Embed(description="❌ No hay nada reproduciéndose.", color=EMBED_COLOR),
                ephemeral=True,
            )
            return
        player.voice_client.pause()
        await interaction.response.send_message(
            embed=discord.Embed(description="⏸️ Reproducción pausada.", color=EMBED_COLOR)
        )

    @app_commands.command(name="resume", description="Reanuda la reproducción pausada.")
    async def resume(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("Solo en servidores.", ephemeral=True)
            return
        player = get_player(interaction.guild.id)
        if not player.voice_client or not player.voice_client.is_paused():
            await interaction.response.send_message(
                embed=discord.Embed(description="❌ La reproducción no está pausada.", color=EMBED_COLOR),
                ephemeral=True,
            )
            return
        player.voice_client.resume()
        await interaction.response.send_message(
            embed=discord.Embed(description="▶️ Reproducción reanudada.", color=EMBED_COLOR)
        )

    @app_commands.command(name="queue", description="Muestra la cola de reproducción actual.")
    async def queue(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("Solo en servidores.", ephemeral=True)
            return
        player = get_player(interaction.guild.id)
        view = QueueView(list(player.queue), player.current)
        await interaction.response.send_message(embed=view.build_embed(), view=view)

    @app_commands.command(name="volume", description="Ajusta el volumen del reproductor (1-100).")
    @app_commands.describe(nivel="Nivel de volumen entre 1 y 100")
    async def volume(
        self,
        interaction: discord.Interaction,
        nivel: app_commands.Range[int, 1, 100],
    ):
        if not interaction.guild:
            await interaction.response.send_message("Solo en servidores.", ephemeral=True)
            return
        player = get_player(interaction.guild.id)
        player.volume = nivel / 100
        if player.voice_client and player.voice_client.source:
            player.voice_client.source.volume = player.volume
        await interaction.response.send_message(
            embed=discord.Embed(
                description=f"🔊 Volumen ajustado a **{nivel}%**.",
                color=EMBED_COLOR,
            )
        )

    @app_commands.command(name="nowplaying", description="Muestra la canción que se está reproduciendo ahora.")
    async def nowplaying(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("Solo en servidores.", ephemeral=True)
            return
        player = get_player(interaction.guild.id)
        if not player.current:
            await interaction.response.send_message(
                embed=discord.Embed(description="❌ No hay nada reproduciéndose.", color=EMBED_COLOR),
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            embed=player.now_playing_embed(),
            view=NowPlayingView(interaction.guild.id),
        )

    @app_commands.command(name="loop", description="Alterna el modo de loop: sin loop / loop canción / loop cola.")
    async def loop(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("Solo en servidores.", ephemeral=True)
            return
        player = get_player(interaction.guild.id)
        player.loop_mode = player.loop_mode.next()
        emoji = {LoopMode.OFF: "🔀", LoopMode.SONG: "🔂", LoopMode.QUEUE: "🔁"}[player.loop_mode]
        await interaction.response.send_message(
            embed=discord.Embed(
                description=f"{emoji} **{player.loop_mode.label()}**",
                color=EMBED_COLOR,
            )
        )

    @app_commands.command(name="shuffle", description="Mezcla aleatoriamente la cola de reproducción.")
    async def shuffle(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("Solo en servidores.", ephemeral=True)
            return
        player = get_player(interaction.guild.id)
        if not player.queue:
            await interaction.response.send_message(
                embed=discord.Embed(description="❌ La cola está vacía.", color=EMBED_COLOR),
                ephemeral=True,
            )
            return
        random.shuffle(player.queue)
        await interaction.response.send_message(
            embed=discord.Embed(
                description=f"🔀 Cola mezclada ({len(player.queue)} canciones).",
                color=EMBED_COLOR,
            )
        )

    @app_commands.command(name="leave", description="El bot abandona el canal de voz.")
    async def leave(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("Solo en servidores.", ephemeral=True)
            return
        player = get_player(interaction.guild.id)
        if not player.voice_client or not player.voice_client.is_connected():
            await interaction.response.send_message(
                embed=discord.Embed(description="❌ No estoy en ningún canal de voz.", color=EMBED_COLOR),
                ephemeral=True,
            )
            return
        await player.cleanup()
        remove_player(interaction.guild.id)
        await interaction.response.send_message(
            embed=discord.Embed(description="👋 Saliendo del canal de voz.", color=EMBED_COLOR)
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Musica(bot))

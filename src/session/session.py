import sqlite3
import discord
from discord.ext import tasks
from contextlib import closing

from .session_dashboard import SessionDashboard
from .session_env import SessionEnvironment
from .session_config import SessionConfig
from .timer import Timer
import asyncio


# Pomodoro Session Class
class Session:
    def __init__(self, bot, name, guild_id, work_time, break_time, repetitions, env, **kwargs):
        self.bot = bot
        self.name = name
        self.config = kwargs.get("config", SessionConfig())
        self.env = env
        # ids
        self.id = kwargs.get("category_id", None)
        self.guild_id = guild_id
        # timer
        self.timer = Timer(self, work_time, break_time, repetitions)
        # user interface
        self.dashboard = SessionDashboard(self)
        # async init
        asyncio.create_task(self.async_init())

    async def async_init(self):
        # wait until environment is fully created
        while not self.env.category:
            await asyncio.sleep(5)
        # set session id
        self.id = self.env.category.id
        # list session instance inside dojo.sessions dict
        self.dojo.active_sessions[self.id] = self
        # setup session env
        await self.env.session_setup()
        self.dojo.start_ids.append(self.env.start_channel_id)
        # creates information embed
        await asyncio.sleep(5)
        if not self.env.info_msg:
            self.env.info_msg = await self.env.info_channel.send(embed=discord.Embed(title=self.name))
            await self.update_dashboard()
        # start auto reset task
        if not self.close_session_if_empty.is_running():
            self.close_session_if_empty.start()
        else:
            self.close_session_if_empty.restart()

    @classmethod
    def new_session(cls, bot, guild_id, name, work_time, break_time, repetitions):
        env = SessionEnvironment.create_new(bot.get_guild(guild_id), name)
        session_instance = cls(bot, name, guild_id, work_time, break_time, repetitions, env)
        asyncio.create_task(session_instance.create_db_entry())
        return session_instance

    @classmethod
    def from_db(cls, session_id, bot):
        with closing(sqlite3.connect("src/dbm/sensei.db")) as conn:
            c = conn.cursor()
            # check if session is in database
            c.execute("SELECT * FROM sessions WHERE id=:id", {"id": session_id})
            result = c.fetchone()
            # return None if session is not in database
            if result:
                # create session instance from database entry and map args
                env = SessionEnvironment.from_database(session_id, bot)
                session_instance = cls(bot, result[1], result[2], result[5], result[6], result[7], env,
                                       category_id=result[0],
                                       config=SessionConfig(mute_admins=result[8]))  # todo mute_members=result[9]))
                return session_instance

    async def create_db_entry(self):
        while not self.id:
            await asyncio.sleep(2)
        with closing(sqlite3.connect("src/dbm/sensei.db")) as conn:
            c = conn.cursor()
            c.execute("SELECT * FROM sessions WHERE id=:id", {"id": self.id})
            result = c.fetchone()
            # check if session already exists
            if not result:
                c.execute("""INSERT INTO sessions VALUES (
                             :id, :name, :guild_id, :info_channel_id, :lobby_channel_id,
                             :work_time, :break_time, :repetitions, :cfg_mute_admins
                         )""",    {"id": self.id,
                                   "name": self.name,
                                   "guild_id": self.guild_id,
                                   "info_channel_id": self.env.info_channel_id,
                                   "lobby_channel_id": self.env.lobby_channel_id,
                                   "work_time": self.timer.work_time,
                                   "break_time": self.timer.break_time,
                                   "repetitions": self.timer.repetitions,
                                   "cfg_mute_admins": self.config.mute_admins})
            conn.commit()

    @property
    def dojo(self):
        return self.bot.dojos[self.guild_id]

    # START

    async def start_session(self):
        # start session timer
        asyncio.create_task(self.timer.start_timer())
        # Logging
        print("Session STARTED - with", self.member_count, "members on guild:", self.dojo.guild.name)

    # NAVIGATION

    async def next_session(self):
        asyncio.create_task(self.update_dashboard())
        await self.env.create_work_channel()
        # move all members to work_channel
        for member in self.env.lobby_channel.members:
            await member.move_to(self.env.work_channel)
            # admins do not get muted automatically
            if member.guild_permissions.administrator and self.dojo.mute_admins:
                await member.edit(mute=True)
        for member in self.env.work_channel.members:
            await member.move_to(self.env.work_channel)
            # admins do not get muted automatically
            if member.guild_permissions.administrator and self.dojo.mute_admins:
                await member.edit(mute=True)
        for member in self.env.start_channel.members:
            await member.move_to(self.env.work_channel)
            # admins do not get muted automatically
            if member.guild_permissions.administrator and self.dojo.mute_admins:
                await member.edit(mute=True)
        # rename session
        session_label = f"Session [ {self.timer.session_count} | {self.timer.repetitions} ]"
        await self.env.work_channel.edit(name=session_label)
        # delete start button
        await self.env.start_channel.delete()

    async def session_break(self):
        # move members to lobby and unmute admins
        await self.reset_members_and_work_channel()

    async def force_break(self, minutes):
        # current session don't count
        if self.timer.session_count > 0:
            self.timer.session_count -= 1
        asyncio.create_task(self.update_dashboard())
        # self.timer.break_time as default value
        if minutes > 120:
            # start normal break
            self.timer.set_time_left(0)
        else:
            # set (new) break_time
            temp = self.timer.break_time
            self.timer.break_time = minutes
            # start a (minutes long) break
            self.timer.set_time_left(0)

            async def set_old_break_time():
                await asyncio.sleep(self.timer.tick)
                self.timer.break_time = temp
                asyncio.create_task(self.update_dashboard())

            asyncio.create_task(set_old_break_time())

    async def stop_session(self):
        # Logging
        print("Session STOPPED - ", self.member_count, " members - ", self.dojo.guild.name)
        # resets
        self.timer.reset()
        await self.reset_members_and_work_channel()
        # delete timer msg
        if self.env.timer_msg:
            await self.env.timer_msg.delete()
            self.env.timer_msg = None
        # clear info_channel
        async for msg in self.env.info_channel.history():
            if msg == self.env.info_msg:
                continue
            else:
                await msg.delete()
        # edit/create info embed
        await self.update_dashboard()

    # TOOLS

    @property
    def member_count(self) -> int:
        member_count = 0
        if self.env.lobby_channel:
            member_count += len(self.env.lobby_channel.members)
        if self.env.work_channel:
            member_count += len(self.env.work_channel.members)
        return member_count

    @property
    async def is_empty(self) -> bool:
        if self.member_count == 0:
            await asyncio.sleep(10)
            return self.member_count == 0
        return False

    @tasks.loop(seconds=5)
    async def close_session_if_empty(self):
        session_is_empty = await self.is_empty
        if session_is_empty:
            asyncio.create_task(self.close_session())

    async def reset_members_and_work_channel(self):
        """ move all members back to lobby and unmute admins """
        for member in self.env.work_channel.members:
            await member.move_to(self.env.lobby_channel)
            # admins do not get unmuted automatically
            if member.guild_permissions.administrator:
                await member.edit(mute=False)
        # only relevant if admin leaves the session early
        for member in self.env.lobby_channel.members:
            if member.guild_permissions.administrator:
                await member.edit(mute=False)
        # todo move this code to sEnv
        # reset work_channel
        if self.env.work_channel:
            await self.env.work_channel.delete()
        # rebuild started session
        await self.env.session_setup()
        self.dojo.start_ids.append(self.env.start_channel_id)

    async def update_dashboard(self):
        """ just pass trough - todo implement this better """
        await self.dashboard.update()

    async def update_edit(self):
        if self.env.category.name != self.name:
            await self.env.category.edit(name=self.name)
        await self.update_dashboard()

    async def close_session(self):
        # turn timer off
        self.close_session_if_empty.stop()
        self.timer.is_active = False
        # disconnect all members and delete channels
        try:
            # work_channel
            for member in self.env.work_channel.members:
                await member.move_to(None)
            await self.env.work_channel.delete()
        except Exception as e:
            print(e)
        try:
            # lobby_channel
            await self.env.start_channel.delete()
            for member in self.env.lobby_channel.members:
                await member.move_to(None)
        except Exception as e:
            print(e)
        # remove active session reference
        del self.dojo.active_sessions[self.id]

    async def dispose(self):
        await self.close_session()
        # remove db entry
        with closing(sqlite3.connect("src/dbm/sensei.db")) as conn:
            c = conn.cursor()
            c.execute("DELETE FROM sessions WHERE id=:id", {"id": self.id})
            conn.commit()
        # remove listener ids
        try:
            self.dojo.lobby_ids.remove(self.env.lobby_channel.id)
        except Exception as e:
            print(e)
        try:
            self.dojo.start_ids.remove(self.env.start_channel_id)
        except Exception as e:
            print(e)
        # dispose environment
        await self.env.dispose()

    def __eq__(self, other):
        return self.id == other.id

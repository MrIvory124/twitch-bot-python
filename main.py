import asyncio
import datetime
import json
import logging
from typing import TYPE_CHECKING

import asqlite
import speech_recognition
import twitchio
from twitchio import eventsub
from twitchio.ext import commands, routines
import threading
import inspect

import ai_responses
import custom_speech_recognition as sr

if TYPE_CHECKING:
    import sqlite3
import os

# links for the user to allow the bot to work
'''
http://localhost:4343/oauth?scopes=user:read:chat%20user:write:chat%20moderator:read:chat_messages%20user:bot%20channel:moderate&force_verify=true - bot
http://localhost:4343/oauth?scopes=channel:bot%20channel:moderate%20user:read:chat&force_verify=true - user ***
'''

LOGGER: logging.Logger = logging.getLogger("Bot")

#TODO Make it so that people in the discord can ask it questions

###### OPTIONS ######
# add any accounts you want to be ignored by the bot, first is streamelements
IGNORELIST = [100135110, 161325782]
BOT_PREFIX = "!"
DEBUG_FLAG = False

### LOADING LOGIN INFORMATION ###
# the config contains all the login information and should be kept from being seen online
with open("config.json", "r") as f:
    config = json.load(f)
    f.close()

CLIENT_ID = config.get("CLIENT_ID")
CLIENT_SECRET = config.get("CLIENT_SECRET")
BOT_ID = config.get("BOT_ID")
OWNER_ID = config.get("OWNER_ID")

'''
The bot class contains all the things that a twitch bot would do.

The first part of the bot is all about signing into the twitch client and configuring it to work with the twitch api. 

It contains timers that fire at specified intervals.
It contains custom commands.
'''
class Bot(commands.AutoBot):
    # Class constructor, we load in all required variables from the config file, and set up the bot
    def __init__(self, *, token_database: asqlite.Pool, subs: list[eventsub.SubscriptionPayload]) -> None:
        self.token_database = token_database
        self.debug_option = DEBUG_FLAG

        super().__init__(client_id=CLIENT_ID, client_secret=CLIENT_SECRET, bot_id=BOT_ID, owner_id=OWNER_ID, prefix=BOT_PREFIX,
            subscriptions=subs, force_subscribe=False, )

    '''
    This section runs when the bot is being setup, timers should be started here.
    The add_component section contains the commands and timers we want to setup, can add more or less if you want the bot to have specific components enabled in it.
    '''
    async def setup_hook(self) -> None:
        # Add our component which contains our commands...
        component = AiChatBotComponent(self)
        await component.setup()
        await self.add_component(component)
        # start timers
        component.ai_reminder.start()
        component.ai_talk.start()

        # enabled debug messages if debug is on
        if self.debug_option:
            twitchio.utils.setup_logging(level=logging.DEBUG)
            old = self.dispatch
            def _trace(name, *a, **k):
                LOGGER.debug("DISPATCH -> %s", name)
                return old(name, *a, **k)

    '''
    This method contains all the logic for if the user send authorisation through the links at the top.
    '''
    async def event_oauth_authorized(self, payload: twitchio.authentication.UserTokenPayload) -> None:
        await self.add_token(payload.access_token, payload.refresh_token)

        if not payload.user_id:
            return

        if payload.user_id == self.bot_id:
            # We usually don't want subscribe to events on the bots channel...
            return

        # A list of subscriptions we would like to make to the newly authorized channel...
        subs: list[eventsub.SubscriptionPayload] = [
            eventsub.ChatMessageSubscription(broadcaster_user_id=payload.user_id, user_id=self.bot_id),
            eventsub.ChatMessageDeleteSubscription(broadcaster_user_id=payload.user_id, user_id=self.bot_id),
            eventsub.ChannelBanSubscription(broadcaster_user_id=payload.user_id, user_id=self.bot_id),
            eventsub.ChannelUnbanSubscription(broadcaster_user_id=payload.user_id, user_id=self.bot_id), ]

        resp: twitchio.MultiSubscribePayload = await self.multi_subscribe(subs)
        if resp.errors:
            LOGGER.warning("Failed to subscribe to: %r, for user: %s", resp.errors, payload.user_id)

    # Returned tokens from the authorisation process are stored in a db
    async def add_token(self, token: str, refresh: str) -> twitchio.authentication.ValidateTokenPayload:
        # Make sure to call super() as it will add the tokens interally and return us some data...
        resp: twitchio.authentication.ValidateTokenPayload = await super().add_token(token, refresh)

        # Store our tokens in a simple SQLite Database when they are authorized...
        query = """
                INSERT INTO tokens (user_id, token, refresh)
                VALUES (?, ?, ?) ON CONFLICT(user_id)
                DO
                UPDATE SET
                    token = excluded.token,
                    refresh = excluded.refresh; \
                """

        async with self.token_database.acquire() as connection:
            await connection.execute(query, (resp.user_id, token, refresh))

        LOGGER.info("Added token to the database for user: %s", resp.user_id)
        return resp

    async def event_ready(self) -> None:
        LOGGER.info("Successfully logged in as: %s", self.bot_id)


'''
    The component class contains everything we want the bot to do.
    Commands, timers and other subscription related things are contained in here
'''
class AiChatBotComponent(commands.Component):
    # An example of a Component with some simple commands and listeners
    # You can use Components within modules for a more organized codebase and hot-reloading.

    # class constructor
    def __init__(self, bot: Bot) -> None:
        # Passing args is not required...
        # We pass bot here as an example...
        self.bot = bot
        self.IFAI = True
        self.msg_database = None  # Will be initialized asynchronously
        self.optout_database = None  # Will be initialized asynchronously
        self.user = bot.create_partialuser(user_id=OWNER_ID)
        # start the bot listening to the mic immediately. Does not start if the ai message generation is off
        if self.IFAI:
            sr.start_listening()

    # When the bot is being setup
    async def setup(self):
        # handles opening both the msgs and user databases
        self.msg_database = await open_msg_db()
        self.optout_database = await open_user_db()
        await self.user.send_message(sender=self.bot.user, message="IM ALIVE!")

    # Listens to incoming message events, and processes them
    # Currently stores them in a db with some information that is gathered alongside it
    @commands.Component.listener("message")
    async def event_message(self, payload: twitchio.ChatMessage) -> None:
        print(f"[{payload.broadcaster.name}] - {payload.chatter.name}: {payload.text}")

        # check to see if user is opted out
        if payload.chatter.id == self.bot.bot_id:
            return  # Ignore messages from the bot itself
        if IGNORELIST.__contains__(int(payload.chatter.id)):
            return  # Do not record people on the ignore list
        # check the excluded users database to see if they opted out
        async with self.optout_database.acquire() as connection:
            query = await connection.fetchall("""SELECT *
                                                 from excluded_users
                                                 WHERE user_id = ?""", (payload.chatter.id,))
            # if they have not opted out, store it in the message db
            if not query:
                await store_user_msg(self.msg_database, payload.id, payload.chatter.id, payload.text)

    '''
    Upon a message being deleted by a bot, remove it from the db
    '''
    @commands.Component.listener()
    async def event_message_delete(self, payload: twitchio.ChatMessageDelete):
        # look at the database to see if the message is in there, then delete
        await self.delete_db_message(payload)

    '''
    Upon a user being banned, delete their messages from the last 30m
    This is in here to avoid bots and vulgar language
    '''
    @commands.Component.listener("event_ban")
    async def event_ban(self, payload: twitchio.ChannelBan) -> None:
        LOGGER.info(
            f"{payload.user.name} was {'perma-banned' if payload.permanent else f'timed out until {payload.ends_at}'} by {payload.moderator.name} — {payload.reason or 'no reason'}")
        # find the messages from user for last 30m and delete them
        # has to be in utc because i actually dont know why at all :)
        cutoff_utc = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=30)
        cutoff_str = cutoff_utc.strftime("%Y-%m-%d %H:%M:%S")

        async with self.msg_database.acquire() as conn:
            await conn.execute("DELETE FROM messages WHERE user_id = ? AND time >= ?", (payload.user.id, cutoff_str), )
            await conn.commit()

    # helper method for deleting messages from the database
    async def delete_db_message(self, payload):
        async with self.msg_database.acquire() as connection:
            await connection.execute("""DELETE
                                        FROM messages
                                        WHERE message_id = ?""", (payload.message_id,))
            LOGGER.info(
                f"Deleted message from {payload.user.name} in channel {payload.broadcaster.name}: {payload.message_id}")


    @commands.command()
    async def optout(self, ctx: commands.Context) -> None:
        """Command that adds the user to the exclude list on the message gathering

        !optout

        Accessible by all users
        """
        # stores the user id as well as current presenting username. Will only search using id in future
        await store_optout_user(self.optout_database, ctx.chatter.id, ctx.chatter.name)
        await ctx.reply(f"You have been opted out of all future message gathering, {ctx.chatter}!")
        await ctx.send(f"For more information visit https://link.mrivory124.com/optout")

    @commands.command()
    async def toggleai(self, ctx: commands.Context) -> None:
        """Command that disables/enables the ai message generation

        !aitoggle

        Accessible by moderators only
        """
        if ctx.author.moderator:
            self.IFAI = not self.IFAI
            await ctx.reply(f"AI message generation: {self.IFAI}")

    @commands.command()
    async def optin(self, ctx: commands.Context) -> None:
        """Command that removes the user from the exclude list on the message gathering

        !optin

        Accessible by all users
        """
        await remove_optout_user(self.optout_database, ctx.chatter.id)
        await ctx.reply(f"You have been opted in to all future message gathering, {ctx.chatter}!")
        await ctx.send(f"For more information visit https://link.mrivory124.com/ai")

    @commands.command()
    async def clip(self, ctx: commands.Context) -> None:
        """Clip command creates a clip from the last 90s

        !clip

        Accessible by all users, has a cooldown to prevent spammage
        """
        print("Hello")

    @routines.routine(delta=datetime.timedelta(seconds=900), wait_first=True)
    async def ai_reminder(self) -> None:
        """A basic routine which reminds users every 15 minutes that messages are being collected.

        This routine will wait 30 minutes first after starting, before making the first iteration.
        """
        await self.user.send_message(sender=self.bot.user,
                                     message="Chat messages are being collected. You can learn more here: https://link.mrivory124.com/ai")

    @routines.routine(delta=datetime.timedelta(seconds=12), wait_first=True)
    async def ai_talk(self) -> None:
        """A basic routine that sends an ai generated image every 60 seconds.

        This routine will wait 60 seconds first after starting, before making the first iteration.
        """
        if not self.IFAI:
            return # do not continue if no ai message generation

        # contains the words recognised from the microphone
        microphone = ""

        try:
            ''' This try method is here because in some cases it does not start
            listening to the microphone before this is run'''
            sr.stop_listening()
            microphone = sr.return_words()
        except speech_recognition.UnknownValueError as e:
            print("Microphone couldn't be stopped, wasn't listening to begin with!")

        LOGGER.info("Generating message...")
        # Query the database for a certain amount of messages from users
        async with self.msg_database.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT * FROM messages ORDER BY time DESC LIMIT 5;")
                rows = await cur.fetchall()
        # combine those messages into one string to send to the ai generation component
        prompt_message = ""
        for r in rows:
            prompt_message += r['message'] + "\n"
        # create a thread to get ai generation, then start the mic listening again
        asyncio.create_task(self._ai_talk_tick(prompt_message, microphone))
        sr.start_listening()

    # helper method for ai message generation
    async def _ai_talk_tick(self, prompt_message: str, streamer_mic_results: str) -> None:
        try:
            if inspect.iscoroutinefunction(ai_responses.response_initial):
                response = await ai_responses.response_initial(prompt_message, streamer_mic_results)
            else:
                # run blocking/sync function without blocking the event loop
                response = await asyncio.to_thread(ai_responses.response_initial, prompt_message, streamer_mic_results)
        except Exception as e:
            LOGGER.error("ai_responses.response failed: %r", e)
            return

        # once the ai message generation is done, send the message
        await self.user.send_message(sender=self.bot.user, message=response)
        LOGGER.warning("Sent a message %s", response)

'''
    # placeholder to remind me how the structure works

    @commands.Component.listener()
    async def event_channel_chat_message_delete(self, payload: twitchio.ChatMessageDelete) -> None:
        """
        Listener for when a moderator deletes a specific chat message.
        """
        LOGGER.info(
            f"Message deleted in channel {payload.broadcaster}: "
            f"Message ID {payload.message_id} from {payload.user}"
        )


    @commands.command()
    async def hi(self, ctx: commands.Context) -> None:
        """Command that replys to the invoker with Hi <name>!

        !hi
        """
        await ctx.reply(f"Hi {ctx.chatter}!")

    @commands.command()
    async def say(self, ctx: commands.Context, *, message: str) -> None:
        """Command which repeats what the invoker sends.

        !say <message>
        """
        await ctx.send(message)

    @commands.command()
    async def add(self, ctx: commands.Context, left: int, right: int) -> None:
        """Command which adds to integers together.

        !add <number> <number>
        """
        await ctx.reply(f"{left} + {right} = {left + right}")

    @commands.command()
    async def choice(self, ctx: commands.Context, *choices: str) -> None:
        """Command which takes in an arbitrary amount of choices and randomly chooses one.

        !choice <choice_1> <choice_2> <choice_3> ...
        """
        await ctx.reply(f"You provided {len(choices)} choices, I choose: {random.choice(choices)}")

    @commands.command(aliases=["thanks", "thank"])
    async def give(self, ctx: commands.Context, user: twitchio.User, amount: int, *, message: str | None = None) -> None:
        """A more advanced example of a command which has makes use of the powerful argument parsing, argument converters and
        aliases.

        The first argument will be attempted to be converted to a User.
        The second argument will be converted to an integer if possible.
        The third argument is optional and will consume the reast of the message.

        !give <@user|user_name> <number> [message]
        !thank <@user|user_name> <number> [message]
        !thanks <@user|user_name> <number> [message]
        """
        msg = f"with message: {message}" if message else ""
        await ctx.send(f"{ctx.chatter.mention} gave {amount} thanks to {user.mention} {msg}")

    @commands.group(invoke_fallback=True)
    async def socials(self, ctx: commands.Context) -> None:
        """Group command for our social links.

        !socials
        """
        await ctx.send("discord.gg/..., youtube.com/..., twitch.tv/...")

    @socials.command(name="discord")
    async def socials_discord(self, ctx: commands.Context) -> None:
        """Sub command of socials that sends only our discord invite.

        !socials discord
        """
        await ctx.send("discord.gg/...")
'''


async def setup_database(db: asqlite.Pool) -> tuple[list[tuple[str, str]], list[eventsub.SubscriptionPayload]]:
    # Create our token table, if it doesn't exist..
    # You should add the created files to .gitignore or potentially store them somewhere safer
    # This is just for example purposes...

    query = """CREATE TABLE IF NOT EXISTS tokens
               (
                   user_id TEXT PRIMARY KEY,
                   token TEXT NOT NULL,
                   refresh TEXT NOT NULL
               )"""


    async with db.acquire() as connection:
        await connection.execute(query)

        # Fetch any existing tokens...
        rows: list[sqlite3.Row] = await connection.fetchall("""SELECT * from tokens""")

        tokens: list[tuple[str, str]] = []
        subs: list[eventsub.SubscriptionPayload] = []

        for row in rows:
            tokens.append((row["token"], row["refresh"]))

            if row["user_id"] == BOT_ID:
                continue

            subs.extend([eventsub.ChatMessageSubscription(broadcaster_user_id=row["user_id"], user_id=BOT_ID),
                eventsub.ChatMessageDeleteSubscription(broadcaster_user_id=row["user_id"], user_id=BOT_ID), ])

    return tokens, subs


async def open_user_db() -> asqlite.Pool:
    user_db_name = "excluded_users.db"
    if not os.path.exists(user_db_name):
        # Create the file by opening a connection and closing it
        async with asqlite.create_pool(user_db_name) as db:
            async with db.acquire() as conn:
                await conn.execute("""CREATE TABLE IF NOT EXISTS excluded_users
                                      (
                                          user_id TEXT PRIMARY KEY,
                                          username TEXT
                                      )""")
                LOGGER.info("Created new user database: %s", user_db_name)
    else:
        LOGGER.info("Using existing user database: %s", user_db_name)
    return await asqlite.create_pool(user_db_name)


# connects to the message database and creates a pool of threads to send information to it
async def open_msg_db() -> asqlite.Pool:
    msg_db_name = "messages.db"
    if not os.path.exists(msg_db_name):
        # Create the file by opening a connection and closing it
        async with asqlite.create_pool(msg_db_name) as db:
            async with db.acquire() as conn:
                await conn.execute("""CREATE TABLE IF NOT EXISTS messages
                                      (
                                          message_id TEXT PRIMARY KEY,
                                          user_id TEXT NOT NULL,
                                          message TEXT NOT NULL,
                                          time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                                      )""")
                LOGGER.info("Created new message database: %s", msg_db_name)
    else:
        LOGGER.info("Using existing message database: %s", msg_db_name)
    return await asqlite.create_pool(msg_db_name)



async def store_user_msg(db: asqlite.Pool, message_id: str, user_id: str, message: str) -> None:
    # helper method for storing messages in the message.db
    async with db.acquire() as connection:
        await connection.execute("""INSERT INTO messages(message_id, user_id, message)
                                    VALUES (?, ?, ?)""", (message_id, user_id, message))



async def store_optout_user(db: asqlite.Pool, user_id: str, username: str) -> None:
    # helper method for storing user optout preferences to the db
    async with db.acquire() as connection:
        await connection.execute("""INSERT INTO excluded_users(user_id, username)
                                    VALUES (?, ?)""", (user_id, username))


async def remove_optout_user(db: asqlite.Pool, user_id: str) -> None:
    # helper method for removing user optout preferences to the db
    async with db.acquire() as connection:
        await connection.execute("""DELETE
                                    FROM excluded_users
                                    WHERE user_id = ?""", (user_id,))


# Our main entry point for our Bot
# Best to setup_logging here, before anything starts
def main() -> None:
    twitchio.utils.setup_logging(level=logging.INFO)

    async def runner() -> None:
        async with asqlite.create_pool("tokens.db") as tdb:
            tokens, subs = await setup_database(tdb)

            async with Bot(token_database=tdb, subs=subs) as bot:
                for pair in tokens:
                    await bot.add_token(*pair)

                await bot.start(load_tokens=False)

    try:
        asyncio.run(runner())
    except KeyboardInterrupt:
        LOGGER.warning("Shutting down due to KeyboardInterrupt")


if __name__ == "__main__":
    main()

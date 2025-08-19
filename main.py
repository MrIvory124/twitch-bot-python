import asyncio
import logging
import random
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import asqlite

import twitchio
from twitchio import eventsub
from twitchio.ext import commands, routines
import datetime
import ai_responses


if TYPE_CHECKING:
    import sqlite3
import os

# scopes for the bot and user
'''
http://localhost:4343/oauth?scopes=user:read:chat%20user:write:chat%20moderator:read:chat_messages%20user:bot%20channel:moderate&force_verify=true - bot
http://localhost:4343/oauth?scopes=channel:bot%20channel:moderate%20user:read:chat&force_verify=true - user ***
'''

LOGGER: logging.Logger = logging.getLogger("Bot")


# TODO remove plain text
# Consider using a .env or another form of Configuration file!
CLIENT_ID: str = "tvjxfyqmsiehcyczmt4s4hw5xczhut"  # The CLIENT ID from the Twitch Dev Console
CLIENT_SECRET: str = "a9orjbywhmgkvfv3gibh2zk82ozo1l"  # The CLIENT SECRET from the Twitch Dev Console
BOT_ID = "1315366618"  # The Account ID of the bot user...
OWNER_ID = "161325782"  # Your personal User ID..

# streamelements,
IGNORELIST = [100135110,161325782]
global IFAI


class Bot(commands.AutoBot):
    def __init__(self, *, token_database: asqlite.Pool, subs: list[eventsub.SubscriptionPayload]) -> None:
        self.token_database = token_database

        super().__init__(
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            bot_id=BOT_ID,
            owner_id=OWNER_ID,
            prefix="!",
            subscriptions=subs,
            force_subscribe=False,
        )

    async def setup_hook(self) -> None:
        # Add our component which contains our commands...
        #await self.add_component(MyComponent(self))
        component = MyComponent(self)
        await component.setup()
        await self.add_component(component)
        IFAI = True
        component.ai_reminder.start()
        component.ai_talk.start()
        '''
        twitchio.utils.setup_logging(level=logging.DEBUG)
        old = self.dispatch
        def _trace(name, *a, **k):
            LOGGER.debug("DISPATCH -> %s", name)
            return old(name, *a, **k)
        #self.dispatch = _trace'''

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
            eventsub.ChannelUnbanSubscription(broadcaster_user_id=payload.user_id, user_id=self.bot_id),
        ]

        resp: twitchio.MultiSubscribePayload = await self.multi_subscribe(subs)
        #LOGGER.error("Created EventSub: %s", [s.type for s in resp.data]) # this line is not being hit
        if resp.errors:
            LOGGER.warning("Failed to subscribe to: %r, for user: %s", resp.errors, payload.user_id)

    async def add_token(self, token: str, refresh: str) -> twitchio.authentication.ValidateTokenPayload:
        # Make sure to call super() as it will add the tokens interally and return us some data...
        resp: twitchio.authentication.ValidateTokenPayload = await super().add_token(token, refresh)

        # Store our tokens in a simple SQLite Database when they are authorized...
        query = """
        INSERT INTO tokens (user_id, token, refresh)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id)
        DO UPDATE SET
            token = excluded.token,
            refresh = excluded.refresh;
        """

        async with self.token_database.acquire() as connection:
            await connection.execute(query, (resp.user_id, token, refresh))

        LOGGER.info("Added token to the database for user: %s", resp.user_id)
        return resp

    async def event_ready(self) -> None:
        LOGGER.info("Successfully logged in as: %s", self.bot_id)


class MyComponent(commands.Component):
    # An example of a Component with some simple commands and listeners
    # You can use Components within modules for a more organized codebase and hot-reloading.

    def __init__(self, bot: Bot) -> None:
        # Passing args is not required...
        # We pass bot here as an example...
        self.bot = bot
        self.msg_database = None  # Will be initialized asynchronously
        self.optout_database = None  # Will be initialized asynchronously
        self.user = bot.create_partialuser(user_id=OWNER_ID)

    async def setup(self):
        # handles opening both the msgs and user databases
        self.msg_database = await open_msg_db()
        self.optout_database = await open_user_db()

    # An example of listening to an event
    # We use a listener in our Component to display the messages received.
    @commands.Component.listener("message")
    async def event_message(self, payload: twitchio.ChatMessage) -> None:
        print(f"[{payload.broadcaster.name}] - {payload.chatter.name}: {payload.text}")
        # check to see if user is opted out
        if payload.chatter.id == self.bot.bot_id:
            return  # Ignore messages from the bot itself
        if IGNORELIST.__contains__(int(payload.chatter.id)):
            return # Do not record people on the ignore list
        async with self.optout_database.acquire() as connection:
            query = await connection.fetchall("""SELECT * from excluded_users WHERE user_id = ?""", (payload.chatter.id,))
            if not query:
                # User has not opted out, store their message
                await store_user_msg(self.msg_database, payload.id, payload.chatter.id, payload.text)

    @commands.Component.listener()
    async def event_message_delete(self, payload: twitchio.ChatMessageDelete):
        # look at the database to see if the message is in there, then delete
        await self.delete_db_message(payload)

    '''
    Upon the user being banned, delete their messages from the last 30m
    This is in here to avoid bots and vulgar language
    '''
    @commands.Component.listener("event_ban")
    async def event_ban(self, payload: twitchio.ChannelBan) -> None:
        LOGGER.info(f"{payload.user.name} was {'perma-banned' if payload.permanent else f'timed out until {payload.ends_at}'} by {payload.moderator.name} — {payload.reason or 'no reason'}")
        # find the messages from user for last 30m and delete them
        # has to be in utc because i actually dont know why at all :)
        cutoff_utc = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=30)
        cutoff_str = cutoff_utc.strftime("%Y-%m-%d %H:%M:%S")

        async with self.msg_database.acquire() as conn:
            await conn.execute(
                "DELETE FROM messages WHERE user_id = ? AND time >= ?",
                (payload.user.id, cutoff_str),
            )
            await conn.commit()

    async def delete_db_message(self, payload):
        async with self.msg_database.acquire() as connection:
            await connection.execute("""DELETE FROM messages WHERE message_id = ?""", (payload.message_id,))
            LOGGER.info(f"Deleted message from {payload.user.name} in channel {payload.broadcaster.name}: {payload.message_id}")


    '''
    @commands.Component.listener()
    async def event_channel_chat_message_delete(self, payload: twitchio.ChatMessageDelete) -> None:
        """
        Listener for when a moderator deletes a specific chat message.
        """
        LOGGER.info(
            f"Message deleted in channel {payload.broadcaster}: "
            f"Message ID {payload.message_id} from {payload.user}"
        )
    '''

    @commands.command()
    async def optout(self, ctx: commands.Context) -> None:
        """Command that adds the user to the exclude list on the message gathering

        !optout
        """
        # stores the user id as well as current presenting username. Will only search using id in future
        await store_optout_user(self.optout_database, ctx.chatter.id, ctx.chatter.name)
        await ctx.reply(f"You have been opted out of all future message gathering, {ctx.chatter}!")
        await ctx.send(f"For more information visit https://link.mrivory124.com/optout")

    @commands.command()
    async def aitoggle(self, ctx: commands.Context) -> None:
        """Command that disables/enables the ai message generation

        !aitoggle
        """
        if ctx.author.moderator:
            ctx.reply(f"AI message generation disabled.")
            IFAI = not IFAI

    @commands.command()
    async def optin(self, ctx: commands.Context) -> None:
        """Command that removes the user from the exclude list on the message gathering

        !optin
        """
        await remove_optout_user(self.optout_database, ctx.chatter.id)
        await ctx.reply(f"You have been opted in to all future message gathering, {ctx.chatter}!")
        await ctx.send(f"For more information visit https://link.mrivory124.com/ai")

    @commands.command()
    async def clip(self, ctx: commands.Context) -> None:
        print("Hello")

    @routines.routine(delta=datetime.timedelta(seconds=1800), wait_first=True)
    async def ai_reminder(self) -> None:
        """A basic routine which does something every 30 minutes.

        This routine will wait 30 minutes first after starting, before making the first iteration.
        """
        await self.user.send_message(sender=self.bot.user, message="Chat messages are being collected. You can learn more here: https://link.mrivory124.com/ai")

    @routines.routine(delta=datetime.timedelta(seconds=60), wait_first=True)
    async def ai_talk(self) -> None:
        """A basic routine that sends an ai generated image every 60 seconds.

        This routine will wait 60 seconds first after starting, before making the first iteration.
        """
        if IFAI:
            LOGGER.info("Generating message...")
            async with self.msg_database.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("SELECT * FROM messages ORDER BY time DESC LIMIT 15;")
                    rows = await cur.fetchall()

            prompt_message = ""
            for r in rows:
                prompt_message += f"{r['message']}\n"

            response = ai_responses.response(prompt_message)
            await self.user.send_message(sender=self.bot.user, message=response)
            LOGGER.info(f"Sent a message {response}")

'''
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

    query = """CREATE TABLE IF NOT EXISTS tokens(user_id TEXT PRIMARY KEY, token TEXT NOT NULL, refresh TEXT NOT NULL)"""
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

            subs.extend([
                    eventsub.ChatMessageSubscription(
                        broadcaster_user_id=row["user_id"], user_id=BOT_ID
                    ),
                    eventsub.ChatMessageDeleteSubscription(
                        broadcaster_user_id=row["user_id"], user_id=BOT_ID
                    ),
                ])

    return tokens, subs

async def open_user_db() -> asqlite.Pool:
    user_db_name = "excluded_users.db"
    if not os.path.exists(user_db_name):
        # Create the file by opening a connection and closing it
        async with asqlite.create_pool(user_db_name) as db:
            async with db.acquire() as conn:
                await conn.execute("""CREATE TABLE IF NOT EXISTS excluded_users(user_id TEXT PRIMARY KEY, username TEXT)""")
                LOGGER.info("Created new user database: %s", user_db_name)
    else:
        LOGGER.info("Using existing user database: %s", user_db_name)
    return await asqlite.create_pool(user_db_name)

async def open_msg_db() -> asqlite.Pool:
    msg_db_name = "messages.db"
    if not os.path.exists(msg_db_name):
        # Create the file by opening a connection and closing it
        async with asqlite.create_pool(msg_db_name) as db:
            async with db.acquire() as conn:
                await conn.execute("""CREATE TABLE IF NOT EXISTS messages(message_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, message TEXT NOT NULL, time TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
                LOGGER.info("Created new message database: %s", msg_db_name)
    else:
        LOGGER.info("Using existing message database: %s", msg_db_name)
    return await asqlite.create_pool(msg_db_name)

async def store_user_msg(db: asqlite.Pool, message_id: str, user_id: str, message: str) -> None:
    async with db.acquire() as connection:
        await connection.execute("""INSERT INTO messages(message_id, user_id, message) VALUES(?, ?, ?)""", (message_id, user_id, message))

async def store_optout_user(db: asqlite.Pool, user_id: str, username: str) -> None:
    async with db.acquire() as connection:
        await connection.execute("""INSERT INTO excluded_users(user_id, username) VALUES(?, ?)""", (user_id, username))

async def remove_optout_user(db: asqlite.Pool, user_id: str) -> None:
    async with db.acquire() as connection:
        await connection.execute("""DELETE FROM excluded_users WHERE user_id = ?""", (user_id,))



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
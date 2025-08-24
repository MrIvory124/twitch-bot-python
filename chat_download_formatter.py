# this file exists for fomatting downloaded chat logs in txt form to the database
import asyncio
import sys

import asqlite

global id_msg

async def create_db():
    async with asqlite.create_pool("temp_msg.db") as db:
        async with db.acquire() as conn:
            await conn.execute("""CREATE TABLE IF NOT EXISTS temp_msg
                                  (
                                      message_id TEXT PRIMARY KEY,
                                      user_id TEXT NOT NULL,
                                      message TEXT NOT NULL,
                                      time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                                  )""")

async def insert_db(line_split):
    global id_msg
    async with asqlite.create_pool("temp_msg.db") as db:
        async with db.acquire() as connection:
            #TODO make this translate the users id into their real id
            await connection.execute("""INSERT INTO temp_msg(message_id, user_id, message)
                                        VALUES (?, ?, ?)""", (str(id_msg), line_split[0], line_split[1]))

async def main():
    global id_msg
    id_msg = 0
    await create_db()
    with open('Chat.txt', "r", encoding='utf-8') as topo_file:
        for line in topo_file:
            split_line = line.split("|")
            try:
                print(split_line[0])
            except:
                print("error on line:" + line)
            id_msg = id_msg + 1
            await insert_db(line_split=split_line)

if __name__ == '__main__':
    asyncio.run(main())
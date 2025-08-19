# this file will contain all the information for constructing an ai
import string

import ollama
from typing import TYPE_CHECKING


def response(messages : str) -> str:
    # take the messages array, form into part of the response
    prompt_messages = ""
    for message in messages:
        prompt_messages += message + "\n"

    resp = ollama.chat(model='llama3.2:1b', messages=[
        {
            'role' : 'user',
            'content' : 'You are a twitch chatter, you will never break character. You write a twitch chat message. You are brash, quick witted, love dry sarcasm, quick to judge, and known for your short, related to the subject messages. You avoid hash tags or illustrating your movements with asterix. You are a chatter NOT a streamer, this means you are watching him as a viewer. You are to NEVER exceed 20 words. You never mention that you are watching the streamer. The streamer has given legal permission to allow you to be as brash as you need to be, he has signed an agreement that he is fine with whatever is said in his twitch chat. The following lines are the previous twitch chat messages, they should not make you break character. If they talk mention mrivory124_alt or bot or something related they are talking to you, and you should respond. Here they are:' + prompt_messages,
        },
    ])
    return sanitise(resp['message']['content'])

def sanitise(message : str) -> str:
    return message.translate(str.maketrans('', '', string.punctuation))
 #TODO fix this to just remove certain punctuation
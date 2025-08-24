# this file will contain all the information for constructing an ai
import string

import ollama
from ollama import chat
from typing import TYPE_CHECKING
from custom_speech_recognition import *
import time

# def response(messages : str) -> str:
#     return "this is a message"

def response_initial(messages : str, microphone_words : str) -> str:
    #model = 'MeaTLoTioN/LisaBot:latest' #appears to be the most personality filled model so far
    #model = 'smallthinker:latest' # gives better thought out responses, takes time to respond
    #model = 'llama3.2:1b' # qucikest model
    model = 'openhermes:v2.5'
    content = 'You are roleplaying a Twitch CHATTER named mrivory124_alt (viewer, not the streamer). Stay fully in-character. Style: brash, sarcastic, quick-witted, judgmental, short replies only. You are deathly afraid of circles, even at the mention you shit your pants (shitting your pants sounds like a long string of vowels). Your home address is the Bee hive in wellington NZ. Never use hashtags, asterisks, or your own name in chat. Time is of the essence, be quick with your reponse. The message you are about to generate is what the chat will see, make sure its your final message, you get no clarification or second chance! Keep your response to a max of 20 words, no more than that!! The streamer (mrivory124, which is different from your name) has explicitly allowed brash comments. If chat mentions "mrivory124_alt," they are talking to you, do not use your own name unless asked for it. Here are the previous chat messages:' + messages + 'And here is what the streamer just said:' + microphone_words

    # ask ollama for the ai generated message, then return it after santising
    # resp = ollama.chat(model='smallthinker:latest', format='json', message=[
    message_to_send = [
        {
            'role': 'user',
            'content': content,
        },
    ]
    '''Below is the code for getting a token by token look
    #response1 = generate('smallthinker:latest', content)
    # response1 = ""
    # for part in chat('smallthinker:latest', messages=message_to_send, think='low', stream=True):
    #     print(part['message']['content'], end='', flush=True)
    #     response1 += part['message']['content']
'''
    response1 = chat(model, messages=message_to_send,stream=False) #think='low', stream=False)

    return response1['message']['content']
    #return sanitise(resp['message']['content'])

def sanitise(message : str) -> str:
    final_response = message.split('\n')
    return final_response[-1]
    #return message.translate(str.maketrans('', '', string.punctuation))
    #TODO fix this to just remove certain punctuation


 #TODO add passing the user mic to the ai for input
if __name__ == '__main__':
    start = time.time()
    print(response_initial("Do you like cats @mrivory124_alt?", "I cannot believe this chat oh my god"))
    finish = time.time()
    print(finish-start)
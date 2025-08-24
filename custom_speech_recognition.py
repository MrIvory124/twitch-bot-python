#!/usr/bin/env python3

# NOTE: this example requires PyAudio because it uses the Microphone class
import time

import speech_recognition as sr

global words
words = ""
global stop_listening

# this is called from the background thread
def callback(recognizer, audio):
    global words
    # received audio data, now we'll recognize it using Whisper from openai
    try:
        words_recognised = recognizer.recognize_whisper(audio, language="english")
        print("Whisper thinks you said " + words_recognised)
        words += words_recognised
    except sr.UnknownValueError:
        print("Whisper could not understand audio")
    except sr.RequestError as e:
        print(f"Could not request results from Whisper; {e}")

def start_listening() -> None:
    r = sr.Recognizer()
    m = sr.Microphone()
    #with m as source:
       # r.adjust_for_ambient_noise(source)  # we only need to calibrate once, before we start listening

    print("Listening")
    global stop_listening
    global words
    words = ""
    # start listening in the background (note that we don't have to do this inside a `with` statement)
    stop_listening = r.listen_in_background(m, callback, phrase_time_limit=None)
    # `stop_listening` is now a function that, when called, stops background listening

def stop_listening():
    global stop_listening
    stop_listening(wait_for_stop=False)
    print("Stopped listening")

def return_words() -> str:
    global words
    return words

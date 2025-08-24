from enum import Enum
import sounddevice as sd
from piper import PiperVoice
import numpy as np

# Load your Piper voice model
# for these do python -m piper.download [name] in console
class Voice(Enum):
    HFC_MALE = "en_US-hfc_male-medium.onnx"
    NE_MALE = "en_GB-northern_english_male-medium.onnx"
    NORMAN_MALE = "en_US-norman-medium.onnx"
    RYAN_MALE = "en_US-ryan-high.onnx"
    SEMAINE_FEMALE = "en_US-semaine_female.onnx"

# default voice
default_voice = Voice.NORMAN_MALE.value

def speak_words(self, model_choice : Voice = default_voice, tts_words : str) -> None:
    model_path = "tts_voice_files/" + model_choice.value

    voice = PiperVoice.load(model_path)

    stream = None
    for chunk in voice.synthesize(tts_words):
        if stream is None:
            # configure output from the first chunk's format
            dtype = 'int16' if chunk.sample_width == 2 else 'int32'
            stream = sd.OutputStream(
                samplerate=chunk.sample_rate,
                channels=chunk.sample_channels,
                dtype=dtype,
            )
            stream.start()

        # chunks are 16-bit PCM in audio_int16_bytes
        sd_data = np.frombuffer(chunk.audio_int16_bytes, dtype=np.int16)
        stream.write(sd_data)

    if stream:
        stream.stop()
        stream.close()
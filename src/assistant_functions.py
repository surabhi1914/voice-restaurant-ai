import os

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

client = OpenAI(
    api_key=os.getenv("GROQ_API_KEY"), base_url="https://api.groq.com/openai/v1"
)


def speak(text):
    response = client.audio.speech.create(
        model="canopylabs/orpheus-v1-english",
        voice="hannah",
        input=text[:200],
        response_format="wav",
    )

    return response.content


def transcribe(audio):
    transcription = client.audio.transcriptions.create(
        model="whisper-large-v3-turbo",
        file=("order.wav", audio.getvalue()),
        language="en",
    )
    return transcription

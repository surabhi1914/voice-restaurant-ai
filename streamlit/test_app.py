import streamlit as st

st.title("Microphone Test")

audio = st.audio_input("Record your voice")

if audio is not None:
    st.success("Recording successful!")
    st.audio(audio)
    st.write("Audio size:", len(audio.getvalue()), "bytes")

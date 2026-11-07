import hashlib
import sys
from pathlib import Path
from uuid import uuid4

import httpx

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
from ai import get_menu, understand_order, validate_order
from src.assistant_functions import speak, transcribe

st.title("DineAssist AI")
st.write("Intelligent Voice Ordering System for Restaurants")


BACKEND_URL = "http://127.0.0.1:8000"

# Clear all the session state variables if the app is reloaded
if "cart" not in st.session_state:
    st.session_state["cart"] = []
if "request_id" not in st.session_state:
    st.session_state["request_id"] = str(uuid4())
if "order_id" not in st.session_state:
    st.session_state["order_id"] = None
if "reply_audio" not in st.session_state:
    st.session_state["reply_audio"] = None
if "submission_started" not in st.session_state:
    st.session_state["submission_started"] = False


if st.session_state.get("pos_synced") and st.button("Start New Order", type="primary"):
    st.session_state["cart"] = []
    st.session_state["validated_order"] = None
    st.session_state["request_id"] = str(uuid4())
    st.session_state["order_id"] = None
    st.session_state["submission_started"] = False
    st.session_state["pos_synced"] = False

    # Clear previous recording and transcription
    st.session_state.pop("transcription", None)
    st.session_state.pop("audio_id", None)
    st.session_state.pop("reply_audio", None)

    st.rerun()


st.header("Voice Order Input - Test")
audio = st.audio_input("Please record your order:")

if audio is not None:
    audio_bytes = audio.getvalue()
    audio_id = hashlib.sha256(audio_bytes).hexdigest()

    # Clear previous results when new audio is recorded
    if st.session_state.get("audio_id") != audio_id:
        st.session_state["audio_id"] = audio_id
        st.session_state.pop("transcription", None)
    st.audio(audio)

    if st.button("Start Transcription"):
        with st.spinner("Transcribing audio..."):
            try:
                # Transcribe the audio using OpenAI's Whisper model
                transcription = transcribe(audio)
                st.session_state["transcription"] = transcription.text
                st.success("Transcription completed successfully!")
            except Exception as e:
                st.error(f"An error occurred: {e}")


if "transcription" in st.session_state:
    st.write("**You Said:**", st.session_state["transcription"])

    if st.button(
        "Process Order", disabled=st.session_state.get("submission_started", False)
    ):
        with st.spinner("Understanding your order..."):
            try:
                parsed = understand_order(
                    st.session_state["transcription"],
                    st.session_state["cart"],
                    get_menu(),
                )

                if parsed.clarification:
                    st.warning(parsed.clarification)

                else:
                    updated_cart = [item.model_dump() for item in parsed.items]
                    if updated_cart:
                        if st.session_state["submission_started"]:
                            st.warning(
                                "Finish the pending order submission "
                                "before making changes."
                            )
                        else:
                            validated = validate_order(updated_cart)

                            if updated_cart != st.session_state["cart"]:
                                st.session_state["request_id"] = str(uuid4())

                            st.session_state["cart"] = updated_cart
                            st.session_state["validated_order"] = validated
                            st.success("Order updated!")

                            # Prepare a short response for the customer
                            item_count = sum(
                                item["quantity"] for item in validated["items"]
                            )
                            item_names = ", ".join(
                                item["name"] for item in validated["items"]
                            )

                            reply = (
                                f"I've updated your order with {item_count} items. "
                                f"Items are {item_names}. "
                                f"Your total is ${validated['total']:.2f}. "
                                "Would you like anything else?"
                            )

                            try:
                                st.session_state["reply_audio"] = speak(reply)
                            except Exception as e:
                                st.session_state.pop("reply_audio", None)
                                st.warning(f"Voice response unavailable: {e}")

            except Exception as e:
                st.error(f"Could not process order: {e}")


if (
    "validated_order" in st.session_state
    and st.session_state["validated_order"] is not None
):
    order = st.session_state["validated_order"]

    st.subheader("Current Order")

    for item in order["items"]:
        mods = ", ".join(item["modifications"]) or "Regular"

        st.write(
            f"{item['quantity']} x {item['name']} ({mods}) - ${item['subtotal']:.2f}"
        )

    st.metric("Total", f"${order['total']:.2f}")


if st.session_state.get("validated_order"):
    if st.session_state["order_id"] is None and st.button(
        "Confirm Order", type="primary"
    ):
        st.session_state["submission_started"] = True

        try:
            # Revalidate current inventory
            validate_order(st.session_state["cart"])

            response = httpx.post(
                f"{BACKEND_URL}/orders",
                json={
                    "request_id": st.session_state["request_id"],
                    "items": st.session_state["cart"],
                },
                timeout=10,
            )
            response.raise_for_status()

            result = response.json()

            st.session_state["order_id"] = result["order_id"]
            st.success(f"Order #{result['order_id']} saved!")

        except Exception as e:
            st.error(
                f"Could not verify order submission: {e}. "
                "Retry confirmation using the same request ID."
            )

    # Synchronize an already-saved order
    if st.session_state["order_id"] is not None:
        if st.session_state.get("pos_synced"):
            st.success("Order successfully sent to the POS!")

        elif st.button("Send Order to POS"):
            try:
                order_id = st.session_state["order_id"]

                response = httpx.post(
                    f"{BACKEND_URL}/orders/{order_id}/sync-pos", timeout=15
                )
                response.raise_for_status()

                st.session_state["pos_synced"] = True
                st.success("Order successfully sent to the POS!")

            except Exception as e:
                st.error(
                    f"POS synchronization failed: {e}. "
                    "Your order is saved. You can retry."
                )


if st.session_state.get("reply_audio"):
    st.subheader("DineAssist Response")

    st.audio(st.session_state["reply_audio"], format="audio/wav")

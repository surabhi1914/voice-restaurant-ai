import hashlib
import json
import logging
import math
import statistics
import sys
import time
from pathlib import Path
from uuid import uuid4

import httpx

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
from ai import get_menu, understand_order, validate_order
from src.assistant_functions import speak, transcribe

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger("dineassist")


def log_event(event, **details):
    logger.info(
        json.dumps(
            {
                "event": event,
                "request_id": st.session_state.get("request_id"),
                **details,
            }
        )
    )


def record_metric(stage, latency, success=True):
    if "metrics_history" not in st.session_state:
        st.session_state["metrics_history"] = []

    st.session_state["metrics_history"].append(
        {"stage": stage, "latency_ms": round(latency * 1000, 2), "success": success}
    )


def percentile(values, p):
    values = sorted(values)
    index = max(0, math.ceil(p / 100 * len(values)) - 1)
    return values[index]


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
                start = time.perf_counter()
                transcription = transcribe(audio)
                st.session_state["transcription_latency"] = time.perf_counter() - start
                st.session_state["transcription"] = transcription.text
                log_event(
                    "transcription_completed",
                    latency_ms=round(
                        st.session_state["transcription_latency"] * 1000, 2
                    ),
                )

                record_metric(
                    "Transcription", st.session_state["transcription_latency"]
                )
                st.success("Transcription completed successfully!")
            except Exception as e:
                st.error(f"An error occurred: {e}")
                record_metric(
                    "Transcription", time.perf_counter() - start, success=False
                )


if "transcription" in st.session_state:
    st.write("**You Said:**", st.session_state["transcription"])

    if st.button(
        "Process Order", disabled=st.session_state.get("submission_started", False)
    ):
        with st.spinner("Understanding your order..."):
            try:
                menu = get_menu()
                start = time.perf_counter()
                parsed = understand_order(
                    st.session_state["transcription"],
                    st.session_state["cart"],
                    menu,
                )
                st.session_state["llm_latency"] = time.perf_counter() - start
                log_event(
                    "llm_latency",
                    latency_ms=round(st.session_state["llm_latency"] * 1000, 2),
                )
                record_metric("LLM", st.session_state["llm_latency"])

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
                            start = time.perf_counter()
                            validated = validate_order(updated_cart)
                            st.session_state["validation_latency"] = (
                                time.perf_counter() - start
                            )
                            log_event(
                                "validation_latency",
                                item_count=sum(
                                    item["quantity"] for item in updated_cart
                                ),
                                latency_ms=round(
                                    st.session_state["validation_latency"] * 1000, 2
                                ),
                            )
                            record_metric(
                                "Validation", st.session_state["validation_latency"]
                            )

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
                                start = time.perf_counter()
                                st.session_state["reply_audio"] = speak(reply)
                                st.session_state["tts_latency"] = (
                                    time.perf_counter() - start
                                )
                                record_metric(
                                    "Text-to-Speech", st.session_state["tts_latency"]
                                )
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
            log_event(
                "order_created", order_id=result["order_id"], total=result["total"]
            )
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
                log_event("pos_synced", order_id=order_id, status="synced")
                st.success("Order successfully sent to the POS!")

            except Exception as e:
                log_event(
                    "pos_sync_failed",
                    order_id=st.session_state["order_id"],
                    error_type=type(e).__name__,
                )
                st.error(
                    f"POS synchronization failed: {e}. "
                    "Your order is saved. You can retry."
                )


if st.session_state.get("reply_audio"):
    st.subheader("DineAssist Response")

    st.audio(st.session_state["reply_audio"], format="audio/wav")


# ---- showing latency metrics for debugging ----#

# st.divider()
# st.subheader("Performance Monitoring")

# metrics = {
#     "Transcription": "transcription_latency",
#     "LLM Processing": "llm_latency",
#     "Backend Validation": "validation_latency",
#     "Text-to-Speech": "tts_latency",
# }

# total = 0

# for label, key in metrics.items():
#     value = st.session_state.get(key)

#     if value is not None:
#         st.write(f"{label}: {value:.3f} seconds")
#         total += value

# st.metric("Measured Processing Time", f"{total:.3f} seconds")


st.divider()
st.subheader("Performance Dashboard")

history = st.session_state.get("metrics_history", [])

if history:
    total = len(history)
    successful = sum(m["success"] for m in history)

    success_rate = successful / total * 100

    st.metric("Operation Success Rate", f"{success_rate:.1f}%")
    st.caption(f"Based on {total} recorded operations")

    results = []

    for stage in sorted(set(m["stage"] for m in history)):
        samples = [
            m["latency_ms"] for m in history if m["stage"] == stage and m["success"]
        ]

        if not samples:
            continue

        results.append(
            {
                "Stage": stage,
                "Requests": len(samples),
                "Average (ms)": round(statistics.mean(samples), 2),
                "p50 (ms)": round(statistics.median(samples), 2),
                "p95 (ms)": round(percentile(samples, 95), 2),
            }
        )

    st.dataframe(results, hide_index=True)

else:
    st.info("No performance measurements collected yet.")

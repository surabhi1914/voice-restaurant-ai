from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    RateLimitError,
)

import streamlit as st


def call_groq(operation, feature, log_event, optional=False):
    try:
        return operation()

    except RateLimitError:
        log_event("groq_rate_limited", feature=feature)
        st.warning(
            f"{feature} is temporarily unavailable because "
            "the AI usage limit was reached. "
            "Please try again later."
        )

    except APITimeoutError:
        log_event("groq_timeout", feature=feature)
        st.error(f"{feature} timed out. Please try again.")

    except APIConnectionError:
        log_event("groq_connection_error", feature=feature)
        st.error(f"{feature} could not connect to the AI service.")

    except APIStatusError as error:
        log_event(
            "groq_api_error",
            feature=feature,
            status_code=error.status_code,
        )
        st.error(f"{feature} is temporarily unavailable.")

    if optional:
        return None

    st.stop()

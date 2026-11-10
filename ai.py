import os
from uuid import uuid4

import httpx
from dotenv import load_dotenv
from openai import OpenAI, OpenAIError

from src.helper_functions import sync_with_pos
from src.models.ai_response import ParsedTurn

load_dotenv()
client = OpenAI(
    api_key=os.getenv("GROQ_API_KEY"), base_url="https://api.groq.com/openai/v1"
)

BACKEND_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:8000")


def get_menu():
    response = httpx.get(f"{BACKEND_URL}/menu", timeout=5)
    response.raise_for_status()
    return response.json()["items"]


def understand_order(message, cart, menu):
    response = client.chat.completions.create(
        model="openai/gpt-oss-20b",
        messages=[
            {
                "role": "system",
                "content": (
                    "You manage a restaurant customer's draft order. "
                    "Return JSON with keys items and clarification. "
                    "items must contain the COMPLETE UPDATED CART, "
                    "not just the latest changes. "
                    "Each item must have item_id, quantity, modifications. "
                    "Use only menu IDs and allowed modifications. "
                    "Keep existing items unchanged unless the customer "
                    "asks to change or remove them. "
                    "Keep separately customized items as separate lines. "
                    "When a customer changes one item, preserve the others. "
                    "If a customer removes a modification, remove it "
                    "from that specific item's modifications list. "
                    "Never invent items, quantities, or customizations. "
                    "If the request is ambiguous or unsupported, "
                    "return the original cart unchanged and ask "
                    "a clarification question. "
                    "Otherwise clarification must be null. "
                    "Never calculate prices."
                ),
            },
            {
                "role": "user",
                "content": (f"Menu: {menu}\nCurrent cart: {cart}\nCustomer: {message}"),
            },
        ],
        response_format={"type": "json_object"},
        temperature=0,
    )

    content = response.choices[0].message.content

    if not content:
        raise ValueError("LLM returned an empty response")

    return ParsedTurn.model_validate_json(content)


def validate_order(cart):
    response = httpx.post(f"{BACKEND_URL}/check-order", json={"items": cart}, timeout=5)

    if not response.is_success:
        raise ValueError(str(response.json()["detail"]))

    return response.json()


def show_order(order):
    print("\nCurrent order:")

    for index, item in enumerate(order["items"], start=1):
        modifications = ", ".join(item["modifications"])
        modifications = modifications or "regular"

        print(
            f"{index}. {item['quantity']} x {item['name']} "
            f"({modifications}) = ${item['subtotal']:.2f}"
        )

    print(f"Total: ${order['total']:.2f}\n")


def main():
    menu = get_menu()
    cart = []
    request_id = str(uuid4())
    uncertain_submission = False

    print("Welcome to our restaurant!")
    print("Type 'confirm' to place your order or 'quit' to exit.\n")

    while True:
        message = input("Customer: ").strip()
        command = message.lower().rstrip(".!")

        if command == "quit":
            break

        if command in (
            "confirm",
            "confirm order",
            "place order",
            "yes, confirm",
            "yes, confirm the order",
        ):
            if not cart:
                print("Assistant: Your cart is empty.")
                continue

            try:
                validate_order(cart)

                response = httpx.post(
                    f"{BACKEND_URL}/orders",
                    json={"request_id": request_id, "items": cart},
                    timeout=5,
                )

                response.raise_for_status()
                result = response.json()

                order_id = result["order_id"]

                print(
                    f"Order #{order_id} saved successfully! "
                    f"Total: ${result['total']:.2f}"
                )

                # Automatically synchronize the saved order
                if sync_with_pos(order_id, BACKEND_URL):
                    print("Assistant: Your order was sent to the kitchen.")
                else:
                    print(
                        "Assistant: Your order was saved, but the "
                        "kitchen system hasn't confirmed receipt. "
                        "The order needs synchronization."
                    )

                break

            except httpx.TimeoutException:
                uncertain_submission = True
                print(
                    "Assistant: Confirmation timed out. "
                    "The order may have been saved. "
                    "Type 'confirm' to safely retry with "
                    "the same request ID."
                )
                continue

            except (ValueError, httpx.HTTPError) as error:
                print("Assistant:", error)
                continue

        if uncertain_submission:
            print(
                "Assistant: Please retry confirmation before "
                "making changes to this order."
            )
            continue

        try:
            parsed = understand_order(message, cart, menu)

            if parsed.clarification:
                print("Assistant:", parsed.clarification)
                continue

            updated_cart = [item.model_dump() for item in parsed.items]

            if not updated_cart:
                cart = []
                request_id = str(uuid4())
                print("Assistant: Your cart is empty.")
                continue

            checked = validate_order(updated_cart)

            # Only accept changes after backend validation
            if updated_cart != cart:
                cart = updated_cart
                request_id = str(uuid4())

            show_order(checked)
            print("Assistant: Anything else?")

        except (ValueError, httpx.HTTPError, OpenAIError) as error:
            print("Assistant:", error)


if __name__ == "__main__":
    main()

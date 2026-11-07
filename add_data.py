from src.db import db_connection

# with db_connection() as conn:
#     conn.execute(
#         """
#             INSERT INTO menu (name, price_cents, stock)
#             VALUES (?, ?, ?)
#             """,
#         ("Coke", 1000, 5),
#     )

#     print("Added the menu item : Sprite")


with db_connection() as conn:
    conn.execute(
        """
            UPDATE menu
            SET stock = 30
            WHERE name = 'Chicken Burger'
            """,
    )

    print("Updated the menu item : Chicken Burger")

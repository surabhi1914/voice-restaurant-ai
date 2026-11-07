from typing import Literal

from pydantic import BaseModel, Field


class POSItem(BaseModel):
    item_id: int
    quantity: int = Field(gt=0)
    modifications: list[str] = Field(default_factory=list)


class POSOrder(BaseModel):
    order_id: int
    items: list[POSItem] = Field(
        min_length=1
    )  # min_length is minimum length of the list
    total_cents: int = Field(gt=0)


class POSWebhook(BaseModel):
    order_id: int
    status: Literal["rejected", "accepted"]


class POSStatusUpdate(BaseModel):
    order_id: int
    status: Literal["accepted", "rejected"]

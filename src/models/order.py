from pydantic import BaseModel, Field


class OrderItem(BaseModel):
    item_id: int
    quantity: int = Field(gt=0)
    modifications: list[str] = Field(default_factory=list)


class OrderRequest(BaseModel):
    items: list[OrderItem] = Field(min_length=1)


class CreateOrderRequest(OrderRequest):
    request_id: str = Field(min_length=1)

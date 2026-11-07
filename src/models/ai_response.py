from pydantic import BaseModel, Field


class OrderItem(BaseModel):
    item_id: int
    quantity: int = Field(gt=0)
    modifications: list[str] = Field(default_factory=list)


# class ParsedOrder(BaseModel):
#     items: list[OrderItem]
#     clarification: str | None


# class Change(BaseModel):
#     item_id: int
#     operation: Literal["add", "set", "remove"]
#     quantity: int | None


class ParsedTurn(BaseModel):
    items: list[OrderItem]
    clarification: str | None

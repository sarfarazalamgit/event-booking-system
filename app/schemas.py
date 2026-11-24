from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

Role = Literal["organizer", "customer"]


class RegisterRequest(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=2, max_length=120)
    password: str = Field(min_length=10, max_length=128)
    role: Role


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    full_name: str
    role: Role
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class EventCreate(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    description: str = Field(default="", max_length=10000)
    location: str = Field(min_length=2, max_length=250)
    start_date: datetime
    capacity: int = Field(gt=0, le=1_000_000)


class EventUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=3, max_length=200)
    description: str | None = Field(default=None, max_length=10000)
    location: str | None = Field(default=None, min_length=2, max_length=250)
    start_date: datetime | None = None
    capacity: int | None = Field(default=None, gt=0, le=1_000_000)

    @model_validator(mode="after")
    def require_update_fields(self):
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to update")
        return self


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    organizer_id: int
    title: str
    description: str
    location: str
    start_date: datetime
    capacity: int
    tickets_sold: int
    created_at: datetime
    updated_at: datetime


class BookingCreate(BaseModel):
    quantity: int = Field(ge=1, le=10)


class BookingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    customer_id: int
    event_id: int
    quantity: int
    status: str
    created_at: datetime

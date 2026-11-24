import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import Depends, FastAPI, HTTPException, Query, Response, status
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import engine, get_db
from app.models import Base, Booking, Event, User
from app.schemas import (
    BookingCreate,
    BookingOut,
    EventCreate,
    EventOut,
    EventUpdate,
    RegisterRequest,
    TokenResponse,
    UserOut,
)
from app.security import (
    CurrentUser,
    CustomerUser,
    OrganizerUser,
    create_access_token,
    hash_password,
    verify_password,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Convenient for local development and this assignment.
    # Use Alembic migrations for production deployments.
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    redis_pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    app.state.redis_pool = redis_pool

    try:
        yield
    finally:
        await redis_pool.aclose()
        await engine.dispose()


app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    description="Role-based event booking API with asynchronous email notifications.",
    lifespan=lifespan,
)


# -------------------------------------------------------------------
# Health checks
# -------------------------------------------------------------------


@app.get("/health")
async def health():
    """Lightweight liveness check: the API process is responding."""
    return {
        "status": "ok",
        "environment": settings.environment,
    }


@app.get("/ready")
async def readiness():
    """Check whether PostgreSQL and Redis are reachable."""
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))

        await app.state.redis_pool.ping()

    except Exception:
        logger.exception("Application readiness check failed")
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "unavailable"},
        )

    return {"status": "ready"}


# -------------------------------------------------------------------
# Authentication
# -------------------------------------------------------------------


@app.post(
    "/auth/register",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
)
async def register(
    payload: RegisterRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    email = str(payload.email).lower()

    existing = await db.scalar(select(User).where(User.email == email))
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

    user = User(
        email=email,
        full_name=payload.full_name,
        password_hash=hash_password(payload.password),
        role=payload.role,
    )
    db.add(user)

    try:
        await db.commit()
        await db.refresh(user)
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        ) from None

    return user


@app.post("/auth/login", response_model=TokenResponse)
async def login(
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    email = form.username.lower()

    user = await db.scalar(select(User).where(User.email == email))

    if user is None or not verify_password(form.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return TokenResponse(access_token=create_access_token(user.id))


@app.get("/users/me", response_model=UserOut)
async def read_me(user: CurrentUser):
    return user


# -------------------------------------------------------------------
# Public event endpoints
# -------------------------------------------------------------------


@app.get("/events", response_model=list[EventOut])
async def list_events(
    db: Annotated[AsyncSession, Depends(get_db)],
    search: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    query = select(Event)

    if search:
        query = query.where(Event.title.ilike(f"%{search}%"))

    query = query.order_by(Event.start_date).limit(limit).offset(offset)

    result = await db.scalars(query)
    return list(result.all())


@app.get("/events/{event_id}", response_model=EventOut)
async def get_event(
    event_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    event = await db.get(Event, event_id)

    if event is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Event not found",
        )

    return event


# -------------------------------------------------------------------
# Organizer event management
# -------------------------------------------------------------------


@app.post(
    "/events",
    response_model=EventOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_event(
    payload: EventCreate,
    user: OrganizerUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    if payload.start_date.tzinfo is None or payload.start_date.utcoffset() is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="start_date must include a timezone",
        )

    event = Event(
        organizer_id=user.id,
        title=payload.title,
        description=payload.description,
        location=payload.location,
        start_date=payload.start_date,
        capacity=payload.capacity,
        tickets_sold=0,
    )

    db.add(event)
    await db.commit()
    await db.refresh(event)

    return event


@app.patch("/events/{event_id}", response_model=EventOut)
async def update_event(
    event_id: int,
    payload: EventUpdate,
    user: OrganizerUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    # Lock the event while checking ownership and changing its fields.
    event = await db.scalar(select(Event).where(Event.id == event_id).with_for_update())

    if event is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Event not found",
        )

    if event.organizer_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not own this event",
        )

    changes = payload.model_dump(exclude_unset=True)

    # These fields are non-nullable in the current Event model.
    # Reject explicit nulls before attempting a database update.
    for field, value in changes.items():
        if value is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"{field} cannot be null",
            )

    if "start_date" in changes:
        new_date = changes["start_date"]

        if new_date.tzinfo is None or new_date.utcoffset() is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="start_date must include a timezone",
            )

    if "capacity" in changes and changes["capacity"] < event.tickets_sold:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Capacity cannot be less than tickets already sold",
        )

    for field, value in changes.items():
        setattr(event, field, value)

    try:
        await db.commit()
        await db.refresh(event)
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Event update conflicts with a database constraint",
        ) from None

    # Notifications are best-effort in this version.
    # A transactional outbox is required to guarantee eventual delivery.
    try:
        result = await db.execute(
            select(User.id, User.email, User.full_name)
            .join(Booking, Booking.customer_id == User.id)
            .where(Booking.event_id == event.id)
            .distinct()
        )

        customers = result.all()

        for customer_id, email, name in customers:
            await app.state.redis_pool.enqueue_job(
                "event_update_notification",
                email,
                name,
                event.title,
                event.location,
                event.start_date.isoformat(),
                _job_id=(
                    f"event-update-{event.id}-{event.updated_at.timestamp()}-{customer_id}"
                ),
            )

    except Exception:
        # The event update has already committed.
        # Do not return an error suggesting that the update was rolled back.
        logger.exception(
            "Event %s was updated, but notification enqueueing failed",
            event.id,
        )

    return event


@app.delete("/events/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_event(
    event_id: int,
    user: OrganizerUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    # Use the same row-locking strategy as the booking endpoint.
    event = await db.scalar(select(Event).where(Event.id == event_id).with_for_update())

    if event is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Event not found",
        )

    if event.organizer_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not own this event",
        )

    if event.tickets_sold > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot delete an event with confirmed bookings",
        )

    try:
        await db.delete(event)
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot delete this event because it has related records",
        ) from None

    return Response(status_code=status.HTTP_204_NO_CONTENT)


# -------------------------------------------------------------------
# Customer bookings
# -------------------------------------------------------------------


@app.post(
    "/events/{event_id}/bookings",
    response_model=BookingOut,
    status_code=status.HTTP_201_CREATED,
)
async def book_event(
    event_id: int,
    payload: BookingCreate,
    user: CustomerUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    # Serialize bookings for the same event using PostgreSQL row locking.
    event = await db.scalar(select(Event).where(Event.id == event_id).with_for_update())

    if event is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Event not found",
        )

    if event.start_date <= datetime.now(UTC):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Event has already started",
        )

    existing = await db.scalar(
        select(Booking).where(
            Booking.customer_id == user.id,
            Booking.event_id == event.id,
        )
    )

    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You already have a booking for this event",
        )

    remaining = event.capacity - event.tickets_sold

    if payload.quantity > remaining:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(f"Not enough tickets available; remaining: {remaining}"),
        )

    booking = Booking(
        customer_id=user.id,
        event_id=event.id,
        quantity=payload.quantity,
        status="confirmed",
    )

    event.tickets_sold += payload.quantity
    db.add(booking)

    try:
        await db.commit()
        await db.refresh(booking)
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Booking conflicts with an existing booking",
        ) from None

    # The booking is committed. Queue failure must not be reported as
    # if the booking itself failed; doing so could encourage unsafe retries.
    try:
        await app.state.redis_pool.enqueue_job(
            "booking_confirmation",
            user.email,
            user.full_name,
            event.title,
            booking.quantity,
            booking.id,
            _job_id=f"booking-confirmation-{booking.id}",
        )
    except Exception:
        logger.exception(
            "Booking %s was committed, but confirmation enqueueing failed",
            booking.id,
        )

    return booking


@app.get("/bookings/me", response_model=list[BookingOut])
async def my_bookings(
    user: CustomerUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    result = await db.scalars(
        select(Booking)
        .where(Booking.customer_id == user.id)
        .order_by(Booking.created_at.desc())
    )

    return list(result.all())

import httpx

from app.config import settings


async def send_email(to_email: str, subject: str, html: str) -> None:
    api_key = (
        settings.resend_api_key.get_secret_value() if settings.resend_api_key else None
    )
    if not api_key:
        raise RuntimeError("RESEND_API_KEY is not configured")

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "from": settings.email_from,
                "to": [to_email],
                "subject": subject,
                "html": html,
            },
        )

        if response.is_error:
            print(
                f"Resend rejected email: status={response.status_code}, "
                f"response={response.text}"
            )

        response.raise_for_status()


async def booking_confirmation(
    ctx,
    recipient: str,
    customer_name: str,
    event_title: str,
    quantity: int,
    booking_id: int,
):
    await send_email(
        recipient,
        f"Booking confirmed: {event_title}",
        (
            f"<h2>Booking confirmed</h2>"
            f"<p>Hello {customer_name},</p>"
            f"<p>Your booking for <strong>{event_title}</strong> is confirmed.</p>"
            f"<p>Tickets: {quantity}</p>"
            f"<p>Booking reference: {booking_id}</p>"
        ),
    )


async def event_update_notification(
    ctx,
    recipient: str,
    customer_name: str,
    event_title: str,
    event_location: str,
    start_date: str,
):
    await send_email(
        recipient,
        f"Event update: {event_title}",
        (
            f"<h2>Event details updated</h2>"
            f"<p>Hello {customer_name},</p>"
            f"<p>The event <strong>{event_title}</strong> has been updated.</p>"
            f"<p>Location: {event_location}</p>"
            f"<p>Start date: {start_date}</p>"
        ),
    )

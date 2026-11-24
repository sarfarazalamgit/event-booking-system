from datetime import UTC, datetime, timedelta


async def register(client, email, role):
    response = await client.post(
        "/auth/register",
        json={
            "email": email,
            "full_name": "Test Person",
            "password": "TestPassword123!",
            "role": role,
        },
    )
    assert response.status_code == 201
    return response.json()


async def login(client, email):
    response = await client.post(
        "/auth/login",
        data={"username": email, "password": "TestPassword123!"},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def create_event(client, headers, capacity=5):
    response = await client.post(
        "/events",
        headers=headers,
        json={
            "title": "Test Event",
            "description": "Integration test",
            "location": "Patna",
            "start_date": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
            "capacity": capacity,
        },
    )
    assert response.status_code == 201
    return response.json()


async def test_health(client):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_register_and_login(client):
    await register(client, "customer1@example.com", "customer")
    headers = await login(client, "customer1@example.com")

    response = await client.get("/users/me", headers=headers)
    assert response.status_code == 200
    assert response.json()["role"] == "customer"


async def test_role_based_access(client):
    await register(client, "organizer1@example.com", "organizer")
    await register(client, "customer2@example.com", "customer")

    organizer_headers = await login(client, "organizer1@example.com")
    customer_headers = await login(client, "customer2@example.com")

    response = await client.post(
        "/events",
        headers=customer_headers,
        json={
            "title": "Unauthorized Event",
            "location": "Patna",
            "start_date": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
            "capacity": 10,
        },
    )
    assert response.status_code == 403

    event = await create_event(client, organizer_headers)
    response = await client.post(
        f"/events/{event['id']}/bookings",
        headers=organizer_headers,
        json={"quantity": 1},
    )
    assert response.status_code == 403


async def test_booking_and_confirmation_job(client):
    await register(client, "organizer2@example.com", "organizer")
    await register(client, "customer3@example.com", "customer")

    organizer_headers = await login(client, "organizer2@example.com")
    customer_headers = await login(client, "customer3@example.com")
    event = await create_event(client, organizer_headers)

    response = await client.post(
        f"/events/{event['id']}/bookings",
        headers=customer_headers,
        json={"quantity": 2},
    )
    assert response.status_code == 201
    assert response.json()["quantity"] == 2

    jobs = client._transport.app.state.redis_pool.jobs
    assert any(job[0] == "booking_confirmation" for job in jobs)

    response = await client.get("/bookings/me", headers=customer_headers)
    assert response.status_code == 200
    assert len(response.json()) == 1


async def test_duplicate_booking_is_rejected(client):
    await register(client, "organizer3@example.com", "organizer")
    await register(client, "customer4@example.com", "customer")

    organizer_headers = await login(client, "organizer3@example.com")
    customer_headers = await login(client, "customer4@example.com")
    event = await create_event(client, organizer_headers)

    first = await client.post(
        f"/events/{event['id']}/bookings",
        headers=customer_headers,
        json={"quantity": 1},
    )
    second = await client.post(
        f"/events/{event['id']}/bookings",
        headers=customer_headers,
        json={"quantity": 1},
    )

    assert first.status_code == 201
    assert second.status_code == 409


async def test_insufficient_inventory(client):
    await register(client, "organizer4@example.com", "organizer")
    await register(client, "customer5@example.com", "customer")

    organizer_headers = await login(client, "organizer4@example.com")
    customer_headers = await login(client, "customer5@example.com")
    event = await create_event(client, organizer_headers, capacity=2)

    response = await client.post(
        f"/events/{event['id']}/bookings",
        headers=customer_headers,
        json={"quantity": 3},
    )
    assert response.status_code == 409


async def test_event_update_enqueues_notifications(client):
    await register(client, "organizer5@example.com", "organizer")
    await register(client, "customer6@example.com", "customer")

    organizer_headers = await login(client, "organizer5@example.com")
    customer_headers = await login(client, "customer6@example.com")
    event = await create_event(client, organizer_headers)

    booking = await client.post(
        f"/events/{event['id']}/bookings",
        headers=customer_headers,
        json={"quantity": 1},
    )
    assert booking.status_code == 201

    response = await client.patch(
        f"/events/{event['id']}",
        headers=organizer_headers,
        json={"location": "New venue"},
    )
    assert response.status_code == 200
    assert response.json()["location"] == "New venue"

    jobs = client._transport.app.state.redis_pool.jobs
    assert any(job[0] == "event_update_notification" for job in jobs)

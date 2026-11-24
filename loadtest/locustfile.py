import os
import uuid

from locust import HttpUser, between, task

EVENT_ID = os.getenv("EVENT_ID")
if not EVENT_ID:
    raise RuntimeError("Set EVENT_ID to an existing event ID")


class BookingUser(HttpUser):
    wait_time = between(0.1, 0.5)

    def on_start(self):
        # Create a unique customer account for each simulated user.
        unique = uuid.uuid4().hex
        self.email = f"load-{unique}@example.com"
        self.password = "LoadTestPassword123!"

        # Track booking attempts separately from successful bookings.
        self.booking_attempted = False
        self.booked = False

        # Register the customer.
        registration = self.client.post(
            "/auth/register",
            json={
                "email": self.email,
                "full_name": "Load Test Customer",
                "password": self.password,
                "role": "customer",
            },
            name="/auth/register",
        )

        if registration.status_code != 201:
            raise RuntimeError(f"Registration failed: {registration.text[:200]}")

        # Log in to obtain an access token.
        login = self.client.post(
            "/auth/login",
            data={
                "username": self.email,
                "password": self.password,
            },
            name="/auth/login",
        )

        if login.status_code != 200:
            raise RuntimeError(f"Login failed: {login.text[:200]}")

        self.client.headers.update(
            {"Authorization": (f"Bearer {login.json()['access_token']}")}
        )

    @task
    def book_event(self):
        # Every customer gets at most one booking attempt.
        if self.booking_attempted:
            return

        # Mark the attempt before sending the request.
        self.booking_attempted = True

        with self.client.post(
            f"/events/{EVENT_ID}/bookings",
            json={"quantity": 1},
            name="/events/{event_id}/bookings",
            catch_response=True,
        ) as response:
            if response.status_code == 201:
                self.booked = True
                response.success()

            elif response.status_code == 409:
                # Print the API's explanation for the conflict.
                print(f"Booking conflict for {self.email}: {response.text[:500]}")

                # Give conflicts their own Locust statistics row.
                response.request_meta["name"] = (
                    "/events/{event_id}/bookings [409 conflict]"
                )

                # A business rejection is not an infrastructure error.
                response.success()

            else:
                # Unexpected HTTP responses count as failures.
                response.failure(
                    f"Unexpected status {response.status_code}: {response.text[:200]}"
                )

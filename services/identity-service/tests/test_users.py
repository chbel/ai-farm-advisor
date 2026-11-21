"""Profile management and admin authorization."""

from tests.constants import DEFAULT_PASSWORD


async def test_read_my_profile(client, register, login):
    user = await register()
    headers = await login()

    response = await client.get("/users/me", headers=headers)

    assert response.status_code == 200
    assert response.json()["id"] == user["id"]


async def test_update_my_profile_changes_only_sent_fields(client, register, login):
    await register(preferred_language="fr")
    headers = await login()

    response = await client.patch("/users/me", json={"preferred_language": "en"}, headers=headers)

    assert response.status_code == 200
    assert response.json()["preferred_language"] == "en"
    assert response.json()["full_name"] == "Test Farmer"


async def test_user_cannot_change_own_role_via_profile(client, register, login):
    await register()
    headers = await login()

    response = await client.patch("/users/me", json={"role": "admin"}, headers=headers)

    assert response.status_code == 200
    assert response.json()["role"] == "farmer"


async def test_change_password(client, register, login):
    await register()
    headers = await login()

    wrong = await client.post(
        "/users/me/password",
        json={"current_password": "not-it", "new_password": "New-Password-1"},
        headers=headers,
    )
    assert wrong.status_code == 400

    ok = await client.post(
        "/users/me/password",
        json={"current_password": DEFAULT_PASSWORD, "new_password": "New-Password-1"},
        headers=headers,
    )
    assert ok.status_code == 204

    old = await client.post(
        "/auth/login", json={"email": "farmer@example.com", "password": DEFAULT_PASSWORD}
    )
    assert old.status_code == 401
    await login(password="New-Password-1")


async def test_non_admin_cannot_use_admin_endpoints(client, register, login):
    user = await register()
    headers = await login()

    assert (await client.get("/users", headers=headers)).status_code == 403
    response = await client.patch(f"/users/{user['id']}", json={"role": "admin"}, headers=headers)
    assert response.status_code == 403


async def test_admin_lists_users_with_pagination(client, register, login, make_admin):
    await register("admin@example.com")
    await make_admin("admin@example.com")
    for i in range(3):
        await register(f"farmer{i}@example.com")
    headers = await login("admin@example.com")

    response = await client.get("/users?limit=2&offset=0", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 4
    assert len(body["items"]) == 2
    assert body["items"][0]["email"] == "farmer2@example.com"  # newest first


async def test_admin_grants_role(client, register, login, make_admin):
    await register("admin@example.com")
    await make_admin("admin@example.com")
    user = await register("tech@example.com")
    headers = await login("admin@example.com")

    response = await client.patch(
        f"/users/{user['id']}", json={"role": "technician"}, headers=headers
    )

    assert response.status_code == 200
    assert response.json()["role"] == "technician"


async def test_admin_cannot_demote_or_deactivate_self(client, register, login, make_admin):
    admin = await register("admin@example.com")
    await make_admin("admin@example.com")
    headers = await login("admin@example.com")

    for change in ({"role": "farmer"}, {"is_active": False}):
        response = await client.patch(f"/users/{admin['id']}", json=change, headers=headers)
        assert response.status_code == 400


async def test_admin_update_unknown_user_returns_404(client, register, login, make_admin):
    await register("admin@example.com")
    await make_admin("admin@example.com")
    headers = await login("admin@example.com")

    response = await client.patch("/users/9999", json={"role": "consultant"}, headers=headers)

    assert response.status_code == 404


async def test_readiness_checks_database(client):
    response = await client.get("/health/ready")

    assert response.status_code == 200
    assert response.json()["database"] == "connected"

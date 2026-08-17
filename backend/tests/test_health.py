from app.core.config import settings

API = settings.api_v1_prefix  # tests stay version-agnostic — bump config, not this file


def test_health_check(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_signup_and_login(client):
    signup_resp = client.post(
        f"{API}/auth/signup",
        json={
            "business_name": "Test Seller Co",
            "email": "owner@testseller.com",
            "password": "supersecret123",
        },
    )
    assert signup_resp.status_code == 201
    token = signup_resp.json()["access_token"]
    assert token

    login_resp = client.post(
        f"{API}/auth/login",
        json={"email": "owner@testseller.com", "password": "supersecret123"},
    )
    assert login_resp.status_code == 200

    me_resp = client.get(
        f"{API}/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert me_resp.status_code == 200
    assert me_resp.json()["email"] == "owner@testseller.com"
    assert me_resp.json()["role"] == "owner"


def test_product_is_scoped_to_business(client):
    signup_resp = client.post(
        f"{API}/auth/signup",
        json={
            "business_name": "Scoped Seller",
            "email": "scoped@testseller.com",
            "password": "supersecret123",
        },
    )
    token = signup_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    create_resp = client.post(
        f"{API}/products",
        json={"sku": "MASTER-001", "name": "Test Product", "selling_price": "999.00"},
        headers=headers,
    )
    assert create_resp.status_code == 201

    list_resp = client.get(f"{API}/products", headers=headers)
    assert list_resp.status_code == 200
    assert len(list_resp.json()) == 1
    assert list_resp.json()[0]["sku"] == "MASTER-001"
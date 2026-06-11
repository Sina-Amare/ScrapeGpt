import asyncio
import httpx

async def test_delete_endpoint():
    async with httpx.AsyncClient() as client:
        # First login
        response = await client.post("http://127.0.0.1:8000/api/v1/auth/login", data={"username": "user@test.com", "password": "password"})
        if response.status_code != 200:
            print("Login failed:", response.text)
            # if login failed, maybe create user?
        
        # let's assume we can get the token somehow, or we can just bypass auth by using a test user?
        pass

if __name__ == "__main__":
    asyncio.run(test_delete_endpoint())

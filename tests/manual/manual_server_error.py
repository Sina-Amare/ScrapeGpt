import asyncio
from fastapi.testclient import TestClient
from app.main import app
from app.api.deps import get_current_user, get_db
from app.models.user import User
from app.models.job import Project
from app.db.database import async_session_factory
from app.services.project_lifecycle import delete_project_tree

async def main():
    # 1. Insert a dummy project
    async with async_session_factory() as db:
        project = Project(
            user_id=1,
            url="https://plati.market/",
            state="COMPLETED",
        )
        db.add(project)
        await db.commit()
        project_id = project.id
        print(f"Created project {project_id}")

    # 2. Setup overrides
    app.dependency_overrides[get_current_user] = lambda: User(id=1, email="test@test.com")
    
    # 3. Use TestClient
    with TestClient(app) as client:
        response = client.delete(f"/api/v1/projects/{project_id}")
        print("Status code:", response.status_code)
        if response.status_code == 500:
            print("Response:", response.text)
        else:
            print("Response:", response.text)

if __name__ == "__main__":
    asyncio.run(main())

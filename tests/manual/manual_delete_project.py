import asyncio
import os
from sqlalchemy import select
from app.db.database import async_session_factory
from app.models.job import Project
from app.services.project_lifecycle import delete_project_tree

async def test_deletion():
    async with async_session_factory() as db:
        result = await db.execute(select(Project).filter(Project.id == 63))
        project = result.scalar_one_or_none()
        if not project:
            print("Project 63 not found. Trying the latest project...")
            result = await db.execute(select(Project).order_by(Project.id.desc()).limit(1))
            project = result.scalar_one_or_none()
            if not project:
                print("No projects found.")
                return
        
        print(f"Attempting to delete project {project.id} with state {project.state}")
        try:
            await delete_project_tree(db, project)
            print("Deletion successful.")
        except Exception as e:
            print("Deletion failed!")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_deletion())

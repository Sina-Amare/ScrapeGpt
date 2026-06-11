import asyncio
from sqlalchemy import select
from app.db.database import async_session_factory
from app.models.job import ExtractionSpec

async def run():
    async with async_session_factory() as db:
        spec = (await db.execute(select(ExtractionSpec).order_by(ExtractionSpec.id.desc()).limit(1))).scalar_one_or_none()
        if spec:
            print(f"Selector: {spec.content_config.get('repeated_item_selector')}")
            for field in spec.fields:
                print(f"Field Type: {field.get('type')} Selector: {field.get('selector')}")

asyncio.run(run())

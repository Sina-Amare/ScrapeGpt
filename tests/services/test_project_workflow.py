from datetime import datetime, timezone

import pytest

from app.models.job import ExtractionMode, ExtractionSpec, Project, ProjectState, RenderMode, WorkflowMode
from app.services.extraction_spec_service import default_spec_from_analysis, selected_field_count
from app.services.project_preview import build_preview_payload


def _project(analysis: dict, mode: ExtractionMode = ExtractionMode.STRUCTURED) -> Project:
    return Project(
        id=1,
        user_id=1,
        url="https://example.com/products",
        extraction_mode=mode,
        workflow_mode=WorkflowMode.GUIDED,
        render_mode=RenderMode.AUTO,
        state=ProjectState.ANALYSIS_READY,
        confidence=0.91,
        warnings=["Review relative URLs"],
        analysis=analysis,
        created_at=datetime.now(timezone.utc),
    )


@pytest.mark.parametrize("confidence,selected", [(0.9, True), (0.69, False)])
def test_default_structured_spec_preserves_field_metadata(confidence, selected):
    project = _project(
        {
            "candidate_fields": [
                {
                    "name": "price",
                    "label": "Price",
                    "selector": ".price",
                    "data_type": "number",
                    "required": True,
                    "confidence": confidence,
                    "sample_values": ["$10"],
                }
            ]
        }
    )

    spec = default_spec_from_analysis(project)

    assert spec["mode"] == ExtractionMode.STRUCTURED
    field = spec["fields"][0]
    assert field["name"] == "price"
    assert field["user_label"] == "Price"
    assert field["selector"] == ".price"
    assert field["type"] == "number"
    assert field["selected"] is selected
    assert field["required"] is True
    assert field["sample_values"] == ["$10"]


def test_default_content_spec_preserves_content_config():
    project = _project(
        {
            "content_type": "documentation",
            "primary_content_selector": "main",
            "recommended_chunking": "section",
            "metadata_fields": [],
        },
        mode=ExtractionMode.CONTENT,
    )

    spec = default_spec_from_analysis(project)

    assert spec["mode"] == ExtractionMode.CONTENT
    assert spec["content_config"]["primary_selector"] == "main"
    assert spec["content_config"]["recommended_chunking"] == "section"
    assert spec["content_config"]["content_type"] == "documentation"


def test_preview_uses_selected_fields_only_and_reports_missing_samples():
    project = _project({})
    spec = ExtractionSpec(
        id=1,
        project_id=project.id,
        mode=ExtractionMode.STRUCTURED,
        fields=[
            {
                "name": "title",
                "label": "Title",
                "user_label": "Book title",
                "selector": "h3 a",
                "type": "string",
                "selected": True,
                "required": True,
                "confidence": 0.99,
                "sample_values": ["A", "B"],
                "warnings": [],
            },
            {
                "name": "unused",
                "label": "Unused",
                "user_label": "Unused",
                "selector": ".unused",
                "type": "string",
                "selected": False,
                "required": False,
                "confidence": 0.2,
                "sample_values": ["hidden"],
                "warnings": [],
            },
            {
                "name": "price",
                "label": "Price",
                "user_label": "Price",
                "selector": ".price",
                "type": "string",
                "selected": True,
                "required": True,
                "confidence": 0.9,
                "sample_values": [],
                "warnings": [],
            },
        ],
        content_config={},
        url_patterns=[],
        page_limit=50,
        export_format="csv",
    )

    preview = build_preview_payload(project, spec)

    assert preview["sample_records"] == [
        {"source_url": project.url, "Book title": "A", "Price": None},
        {"source_url": project.url, "Book title": "B", "Price": None},
    ]
    assert preview["missing_fields"][0]["name"] == "price"
    assert preview["quality_summary"]["selected_field_count"] == 2
    assert selected_field_count(spec) == 2

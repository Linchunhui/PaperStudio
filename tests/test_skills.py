"""Guard the source files that runtime jobs load and freeze as role prompts."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "skills"


def test_registry_entries_are_complete_and_safe():
    registry = json.loads((ROOT / "registry.json").read_text())
    assert registry["schema_version"] == 1
    entries = registry["skills"]
    assert len({item["id"] for item in entries}) == len(entries)
    job_kinds = []
    for item in entries:
        skill_root = (ROOT / item["id"]).resolve()
        assert skill_root.parent == ROOT.resolve()
        entrypoint = (ROOT / item["entrypoint"]).resolve()
        assert entrypoint == skill_root / "SKILL.md"
        source = entrypoint.read_text()
        frontmatter = source.split("---", 2)[1]
        assert f'name: {item["id"]}' in frontmatter
        assert 'description: "' in frontmatter
        assert item["description"] in frontmatter
        assert item["name"] and item["modes"]
        for reference in item["references"]:
            path = (ROOT / reference).resolve()
            assert path.parent == skill_root / "references"
            assert path.is_file() and path.read_text().strip()
        for target in re.findall(r'\]\(([^)]+)\)', source):
            if target.startswith(("https://", "http://", "#")):
                continue
            path = (skill_root / target).resolve()
            assert path.is_relative_to(skill_root)
            assert path.is_file(), f"Missing skill reference: {target}"
        job_kinds.extend(item["job_kinds"])
    assert set(job_kinds) == {"guide", "brief", "selection-summary", "translate", "paragraph-translate", "notes-summary"}
    assert len(job_kinds) == len(set(job_kinds))

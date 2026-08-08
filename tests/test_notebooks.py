import json
from pathlib import Path


def test_notebooks_do_not_commit_outputs() -> None:
    notebooks = sorted((Path(__file__).parents[1] / "notebooks").glob("*.ipynb"))

    assert notebooks
    for notebook_path in notebooks:
        notebook = json.loads(notebook_path.read_text(encoding="utf-8"))
        for cell in notebook["cells"]:
            if cell["cell_type"] == "code":
                assert cell["execution_count"] is None
                assert cell["outputs"] == []

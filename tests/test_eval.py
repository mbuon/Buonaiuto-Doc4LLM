from __future__ import annotations

import json
import subprocess
from pathlib import Path


def test_eval_script_computes_mrr_and_ndcg(tmp_path: Path) -> None:
    fixtures_path = tmp_path / "benchmark.json"
    fixtures_path.write_text(
        json.dumps(
            [
                {
                    "query": "react hooks",
                    "expected_uri": "doc://react/docs/hooks.md",
                    "ranked_uris": [
                        "doc://react/docs/hooks.md",
                        "doc://react/docs/effects.md",
                    ],
                },
                {
                    "query": "python pathlib",
                    "expected_uri": "doc://python/docs/pathlib.md",
                    "ranked_uris": [
                        "doc://python/docs/os.md",
                        "doc://python/docs/pathlib.md",
                    ],
                },
            ]
        ),
        encoding="utf-8",
    )

    repo_root = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        [
            "python3",
            str(repo_root / "scripts/eval.py"),
            "--fixtures",
            str(fixtures_path),
            "--k",
            "10",
            "--min-mrr",
            "0.7",
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert payload["total_queries"] == 2
    assert payload["mrr_at_k"] == 0.75
    assert payload["ndcg_at_k"] == 0.8155

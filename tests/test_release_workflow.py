from pathlib import Path


def test_workflows_pin_latest_action_releases():
    action_refs = {
        line.strip().removeprefix("uses: ")
        for workflow in Path(".github/workflows").glob("*.yml")
        for line in workflow.read_text().splitlines()
        if line.strip().startswith("uses: ")
    }

    assert action_refs == {
        "actions/checkout@v7.0.1",
        "actions/setup-node@v7.0.0",
        "astral-sh/setup-uv@v9.0.0",
    }


def test_published_release_deploys_the_app_without_publishing_the_board():
    workflow = Path(".github/workflows/release-app.yml").read_text()

    assert "release:" in workflow
    assert "types: [published]" in workflow
    assert "run: make deploy-app" in workflow
    assert "CLOUDFLARE_API_TOKEN: ${{ secrets.CLOUDFLARE_API_TOKEN }}" in workflow
    assert "CLOUDFLARE_ACCOUNT_ID: ${{ secrets.CLOUDFLARE_ACCOUNT_ID }}" in workflow
    assert "deploy-board" not in workflow
    assert "deploy-all" not in workflow

"""The multi-user compose keeps saved secrets readable across rebuilds."""
from pathlib import Path

import yaml

COMPOSE = Path(__file__).resolve().parents[2] / "deploy" / "docker-compose.keycloak.yml"


class _ComposeLoader(yaml.SafeLoader):
    """SafeLoader that accepts Compose's !override / !reset merge tags."""


_ComposeLoader.add_multi_constructor(
    "!", lambda loader, suffix, node: loader.construct_object(node, deep=True)
    if not isinstance(node, yaml.ScalarNode) else loader.construct_scalar(node)
)


def test_app_container_has_a_fixed_hostname():
    # The image's /etc/machine-id is empty, so services/_secret_key.py derives
    # the key for saved LLM keys and the HF token from hostname + user. The
    # default hostname (container id) changes on every rebuild.
    service = yaml.load(COMPOSE.read_text(encoding="utf-8"), Loader=_ComposeLoader)[
        "services"]["voicestudio"]
    assert service.get("hostname") == "voicestudio"

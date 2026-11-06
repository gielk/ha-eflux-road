"""Home Assistant runtime tests for the custom integration."""

pytest_plugins = "pytest_homeassistant_custom_component"


import pytest


@pytest.fixture(autouse=True)
def enable_custom(enable_custom_integrations):
    yield

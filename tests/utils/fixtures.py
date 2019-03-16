"""This module provides utility fixtures for testing."""
from typing import Tuple

from _pytest.fixtures import FixtureRequest
import pytest


@pytest.fixture(params=[True, False])
def boolean(request: FixtureRequest) -> bool:
    return request.param


@pytest.fixture
def boolean1(boolean: bool) -> Tuple[bool]:
    return boolean,


@pytest.fixture(params=[True, False])
def boolean2(request: FixtureRequest, boolean: bool) -> Tuple[bool, bool]:
    return boolean, request.param


@pytest.fixture(params=[True, False])
def boolean3(request: FixtureRequest, boolean2: Tuple[bool, bool]) -> Tuple[bool, bool, bool]:
    return boolean2[0], boolean2[1], request.param

# …

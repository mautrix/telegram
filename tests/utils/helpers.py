"""This module provides utility functions for testing."""
from typing import Generator, Tuple
from unittest.mock import Mock


def AsyncMock(*args, **kwargs):
    """Mocks a asyncronous coroutine which can be called with 'await'."""
    m = Mock(*args, **kwargs)

    async def mock_coro(*args, **kwargs):
        return m(*args, **kwargs)

    mock_coro.mock = m
    return mock_coro


def list_true_once_each(length: int) -> Generator[Tuple[bool, ...], None, None]:
    """Yields tuples of bools with exactly one entry being True, starting left.

    Args:
        length: Length of the resulting tuples
    """
    for i in range(length):
        yield tuple(i == j for j in range(length))

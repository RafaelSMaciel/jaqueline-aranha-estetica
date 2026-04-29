"""Fixtures pytest-django reaproveitaveis."""
import pytest

from aranha_estetica.tests.factories import (
    criar_cliente,
    criar_procedimento,
    criar_profissional,
)


@pytest.fixture
def cliente(db):
    return criar_cliente()


@pytest.fixture
def profissional(db):
    return criar_profissional()


@pytest.fixture
def procedimento(db):
    return criar_procedimento()

import pytest
import sys
import tempfile
import os

# A suíte testa handlers diretamente e não é uma implantação do dashboard. Os
# testes específicos de autenticação habilitam uma instância isolada e cobrem o
# fail-closed, cookie e CSRF sem depender do .env real do desenvolvedor.
os.environ["PYTHON_DOTENV_DISABLED"] = "1"
os.environ["DASHBOARD_AUTH_ENABLED"] = "false"

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

from jarvis.database.persistence import Persistence

@pytest.fixture(autouse=True)
def temp_db():
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    Persistence.set_db_path(db_path)
    Persistence.init_db()
    yield
    Persistence.set_db_path(None)
    try:
        os.unlink(db_path)
    except:
        pass

"""Always use a unique temporary database, including when one test file is run."""
import os
import tempfile
from pathlib import Path

_test_dir=tempfile.TemporaryDirectory(prefix='vmeste_tests_')
os.environ['DATABASE_URL']='sqlite:///'+str(Path(_test_dir.name)/'tests.db')
os.environ['SECRET_KEY']='test-secret'
os.environ['ENABLE_SCHEDULER']='0'
os.environ['MGSU_SOURCE']='raspika_public'

def pytest_sessionfinish(session,exitstatus):
    from app.database import engine
    engine.dispose()
    _test_dir.cleanup()

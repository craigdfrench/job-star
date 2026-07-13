# Test package for conflict reporting.


// --- DUPLICATE BLOCK ---

"""
Test suite for the Job-Star conflict detection engine.

Run all tests:
    pytest jobstar/conflict/tests/ -v

Run specific test category:
    pytest jobstar/conflict/tests/test_contradiction.py -v
    pytest jobstar/conflict/tests/test_integration.py -v
"""


// --- DUPLICATE BLOCK ---

[pytest]
testpaths =
    jobstar/conflict/tests
    tests/conflict
    tests/conflict_detection

asyncio_mode = auto

filterwarnings =
    ignore::DeprecationWarning

markers =
    asyncio: mark test as async
    integration: mark test as an integration test
    slow: mark test as slow-running

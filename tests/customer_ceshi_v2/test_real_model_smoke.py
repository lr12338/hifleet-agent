import os

import pytest


@pytest.mark.skipif(os.getenv("RUN_CUSTOMER_CESHI_MODEL_TESTS") != "1", reason="requires explicitly enabled non-production model credentials")
def test_real_model_suite_is_explicitly_opt_in():
    pytest.skip("Real-model smoke coverage must run against dedicated test credentials and synthetic media.")

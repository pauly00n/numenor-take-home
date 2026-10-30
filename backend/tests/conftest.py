import os

from hypothesis import HealthCheck, settings

# HYPOTHESIS_PROFILE=heavy uv run pytest  for a long stress run.
settings.register_profile("default", max_examples=200, deadline=None)
settings.register_profile(
    "heavy", max_examples=5000, deadline=None, suppress_health_check=[HealthCheck.too_slow]
)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "default"))

import ga4_analytics
from ga4_analytics import config


def test_package_imports():
    assert ga4_analytics.__version__ == "0.1.0"


def test_window_is_the_ga4_sample_window():
    assert (config.START_SUFFIX, config.END_SUFFIX) == ("20201101", "20210131")

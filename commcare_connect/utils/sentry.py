# Requests to these paths are never traced: load balancers, uptime monitors and the status page
# poll them constantly.
UNSAMPLED_PATHS = ("/up", "/up/", "/export/up", "/export/up/")
UNSAMPLED_PATH_PREFIXES = ("/status/",)


def make_traces_sampler(sample_rate):
    """Return a Sentry ``traces_sampler`` that samples at ``sample_rate``, except health checks."""

    def traces_sampler(sampling_context):
        path = sampling_context.get("wsgi_environ", {}).get("PATH_INFO", "")
        if path in UNSAMPLED_PATHS or path.startswith(UNSAMPLED_PATH_PREFIXES):
            return 0
        parent_sampled = sampling_context.get("parent_sampled")
        if parent_sampled is not None:
            return float(parent_sampled)
        return sample_rate

    return traces_sampler

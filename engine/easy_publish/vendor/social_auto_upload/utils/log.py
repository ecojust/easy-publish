"""No upstream file logging, cookie paths, payloads or stdout protocol pollution."""


class _QuietLogger:
    # Browser failures are translated at the provider boundary. Raw upstream
    # exception text may include credentials or HTML, and is not product output.
    def info(self, *args, **kwargs):
        pass

    success = info
    error = info
    warning = info
    debug = info


dayu_logger = _QuietLogger()

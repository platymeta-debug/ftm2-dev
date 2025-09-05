class HttpDriver:
    def __init__(self):
        self._client = None
        self._mode = None

    def start(self):
        try:
            import httpx
            self._client = httpx.Client(timeout=10.0)
            self._mode = "httpx"
        except Exception:
            try:
                import requests
                self._client = None  # requests is module-based
                self._mode = "requests"
            except Exception:
                raise RuntimeError("HTTP driver not available: pip install httpx or requests")

    def get(self, url, params=None):
        if self._mode == "httpx":
            import httpx
            r = self._client.get(url, params=params)
            r.raise_for_status()
            return r.json()
        elif self._mode == "requests":
            import requests
            r = requests.get(url, params=params, timeout=10.0)
            r.raise_for_status()
            return r.json()
        else:
            raise RuntimeError("HTTP driver not started")

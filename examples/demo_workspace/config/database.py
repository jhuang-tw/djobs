"""Database configuration and connection pool management."""


class DatabaseConfig:
    def __init__(self, url: str, pool_size: int = 5, echo: bool = False):
        self.url = url
        self.pool_size = pool_size
        self.echo = echo
        self._connection = None

    def connect(self):
        self._connection = f"connected:{self.url}"
        return self._connection

    def disconnect(self):
        self._connection = None

    def is_connected(self) -> bool:
        return self._connection is not None

    def get_connection(self):
        if not self.is_connected():
            self.connect()
        return self._connection

class ConnectionPool:
    def __init__(self, config: DatabaseConfig, max_size: int = 10):
        self.config = config
        self.max_size = max_size
        self._pool: list = []
        self._in_use: int = 0

    def acquire(self):
        if self._pool:
            self._in_use += 1
            return self._pool.pop()
        if self._in_use < self.max_size:
            self._in_use += 1
            return self.config.connect()
        raise RuntimeError("Connection pool exhausted")

    def release(self, conn) -> None:
        self._pool.append(conn)
        self._in_use -= 1

    def size(self) -> int:
        return len(self._pool) + self._in_use

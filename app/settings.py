"""Env-driven settings."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # HTTP
    http_host: str = "0.0.0.0"  # noqa: S104 — bound to Tailscale on ai-primary
    http_port: int = 8090

    # Upstream service URLs (docker-network DNS names)
    redis_url: str = "redis://redis:6379/0"
    postgres_dsn: str = "postgresql://benadmin:CHANGEME@postgres:5432/aicore"
    ocde_url: str = "http://ocde:8014"
    oms_gateway_url: str = "http://oms-gateway:8003"
    strategy_runners_url: str = "http://strategy-runners:8006"
    liquidation_bot_url: str = "http://liquidation-bot:8011"

    # Polling cadence
    state_cache_ttl_sec: float = 1.5    # cache the aggregated /api/state for N seconds

    # Mock fallback when upstream is unreachable (development + isolation)
    use_mock_fallback: bool = True

    # Capital reserved for gas — not in any single position, surfaced as
    # the liquidation-bot's "capital" field and the working-capital top-up.
    reserved_gas_usd: float = 200.0

    # Oracle freshness — which Redis keys we ping for system health.
    # Sync these with what the upstream feeders actually publish; otherwise
    # the dashboard reports false negatives (e.g. wbtc/wsteth never streamed).
    cl_assets: list[str] = [
        "btc", "eth", "sol", "bnb", "xrp", "doge", "hype",
    ]
    pyth_assets: list[str] = [
        "aave", "ada", "apt", "arb", "atom", "avax", "bnb", "btc",
        "cbeth", "doge", "dot", "eth", "hype", "inj", "link", "ltc",
        "near", "op", "pepe", "pol", "shib", "sol", "sui", "tia",
        "ton", "trx", "uni", "usdc", "usdt", "wsteth", "xrp",
    ]

    # SSE
    sse_buffer_size: int = 50            # max events buffered per subscriber
    sse_channels: list[str] = ["exec.*", "sig.*", "warn.*", "err.*"]

    # Kill switch
    halt_key_ttl_sec: int = 7 * 24 * 3600   # 7 days
    halt_strategies: list[str] = ["polymarket", "liquidation"]

    # Audit log file
    audit_log_path: str = "/var/log/home-dashboard/audit.log"

    log_level: str = "INFO"


settings = Settings()

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
    cl_assets: list[str] = [
        "btc", "eth", "wsteth", "cbeth", "usdc", "usdt", "wbtc",
    ]
    pyth_assets: list[str] = [
        # 31 assets; truncated default — production env overrides.
        "btc", "eth", "sol", "matic", "avax", "doge", "bnb", "ada",
        "xrp", "dot", "ltc", "uni", "link", "atom", "near", "ftm",
        "op", "arb", "sui", "apt", "inj", "ondo", "rndr", "tia",
        "sei", "wld", "hype", "pyth", "jto", "wif", "bonk",
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

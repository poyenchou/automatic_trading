import structlog

from broker.client import AlpacaClient
from config.settings import Settings
from market_data.models import ScreenerResult

log = structlog.get_logger(__name__)


class TopMoversScreener:
    def __init__(self, client: AlpacaClient, settings: Settings) -> None:
        self._client = client
        self._settings = settings

    def get_top_movers(self) -> list[ScreenerResult]:
        """
        Fetch the most active stocks by volume and return them as ScreenerResults.
        Returns [] if no results. BrokerError subclasses propagate to the caller.
        """
        rows, last_updated = self._client.get_top_movers(top=self._settings.num_movers)
        log.debug("screener.top_movers", count=len(rows), last_updated=last_updated)
        return [ScreenerResult(symbol=row.symbol, volume=row.volume) for row in rows]

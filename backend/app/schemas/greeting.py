from datetime import date, datetime

from pydantic import ConfigDict

from app.schemas.common import UTCResponseModel


class GreetingRead(UTCResponseModel):
    """Der Willkommenssatz des Dashboards.

    ``source`` sagt, woher der Satz stammt: ``ai``, wenn der Provider geantwortet
    hat, und ``fallback``, wenn der Satz aus den Daten gebaut wurde. Die Oberflaeche
    zeigt das als kleinen Hinweis, damit ein Satz nicht als KI-Aussage missverstanden
    wird, den kein Modell geschrieben hat.
    """

    model_config = ConfigDict(extra="forbid")

    text: str
    source: str
    model: str | None = None
    generated_at: datetime
    generated_on: date

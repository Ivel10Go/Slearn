"""Punkte und Auszeichnungen unter ``/api/v1/rewards``.

**Eine** Route, nicht drei. Kennzahl, Auszeichnungen und Buchungen beantworten
dieselbe Frage und werden immer zusammen gelesen; drei Endpunkte wuerden drei
Anfragen und drei Moeglichkeiten schaffen, in denen die Karte einen halben Stand
zeigt - eine Punktzahl ohne die Buchung, aus der sie stammt, oder eine
Auszeichnung ohne ihren Fortschritt. Das ist die einzige Begruendung, die hier
zählt, und sie ist dieselbe, mit der auch ``useGrades`` drei Getter **in einem**
Hook und nicht in drei Karten aufruft.

Die Buchung selbst laeuft ueber ``app/services/rewards.py`` und haengt an den
Abschluss-, Stopp- und Patch-Endpunkten der Aufgaben - dort, wo das Ereignis
stattfindet, und nicht hier.
"""

from fastapi import APIRouter, Query

from app.api.deps import AppSettings, CurrentUser, DatabaseSession
from app.core.time import resolve_timezone_name, zone_or_utc
from app.schemas.rewards import RewardOverview
from app.services import rewards as rewards_service

router = APIRouter(prefix="/api/v1/rewards", tags=["rewards"])


@router.get("", response_model=RewardOverview)
def read_rewards(
    current_user: CurrentUser,
    db: DatabaseSession,
    settings: AppSettings,
    limit: int = Query(default=rewards_service.ENTRY_LIMIT, ge=1, le=rewards_service.ENTRY_LIMIT_MAX),
) -> RewardOverview:
    """Punktekonto, Auszeichnungen und die letzten Buchungen.

    ``limit`` begrenzt die **Liste** der Buchungen, nicht die Summe:
    ``summary.total_points`` laeuft ueber alle Zeilen. Sonst wuerde die Karte
    irgendwann die ganze Historie in den Browser ziehen.

    Alles rechnet in der Zeitzone des Nutzers. Ein heute frueh begonnener Lernblock
    ist in ``Europe/Berlin`` heute und in UTC vielleicht gestern - fuer die
    Tagesobergrenze macht genau dieser Unterschied ein Viertel des Punktekontos aus.
    """
    timezone_name = resolve_timezone_name(current_user.timezone, settings.timezone)
    stand = rewards_service.rewards_read(
        db, current_user.id, zone_or_utc(timezone_name), timezone_name, entries_limit=limit
    )
    return RewardOverview(
        summary=stand.summary,
        rules=rewards_service.reward_rules(),
        catalog=rewards_service.badge_definitions(),
        badges=stand.badges,
        entries=stand.entries,
    )

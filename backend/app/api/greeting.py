"""Der Willkommenssatz des Dashboards.

Bewusst zwei Routen: ``GET`` liefert den Satz aus dem Tages-Cache und erzeugt ihn
nur einmal, ``POST`` erzwingt eine Neugenerierung. Dadurch kostet ein normaler
Seitenaufruf keinen Modelllauf, ein Klick auf "Neu formulieren" aber schon.
"""

from datetime import datetime

from fastapi import APIRouter

from app.api.deps import AppSettings, CurrentUser, DatabaseSession, RequestLanguage
from app.core.time import as_utc, resolve_timezone_name, utc_now, zone_or_utc
from app.db.entities import GreetingCache
from app.schemas.dashboard import DashboardResponse
from app.schemas.greeting import GreetingRead
from app.services import ai_routing
from app.services import grades as grades_service
from app.services.dashboard import serialize_dashboard
from app.services.greeting import greeting_cache_is_fresh, render_greeting

router = APIRouter(prefix="/api/v1/greeting", tags=["greeting"])


def _read(text: str, source: str, model: str | None, generated_at: datetime) -> GreetingRead:
    """Baut die Antwort - und **normalisiert** die Herkunft.

    Der Normalisierungsschritt ist kein Kosmetik: ``greeting_cache.source`` kann
    noch ``ai`` enthalten, weil die Zeilen vor dieser Aenderung geschrieben
    wurden. Ohne den Schritt erschiene ein heute noch gueltiger Cacheeintrag als
    ``fallback`` - also als Datenluege in genau dem Feld, das Datenluegen verhindern
    soll.
    """
    stamp = as_utc(generated_at)
    return GreetingRead(
        text=text,
        source=ai_routing.normalize_source(source),
        model=model,
        generated_at=stamp,
        generated_on=stamp.date(),
    )


@router.get("", response_model=GreetingRead)
def read_greeting(
    current_user: CurrentUser,
    db: DatabaseSession,
    settings: AppSettings,
    language: RequestLanguage,
) -> GreetingRead:
    """Liefert den Satz des Tages, sonst wird er einmal erzeugt - je Sprache der Oberflaeche."""
    now = utc_now()
    zone = zone_or_utc(resolve_timezone_name(current_user.timezone or settings.timezone, settings.timezone))
    heute = now.astimezone(zone).date()

    stored = db.query(GreetingCache).filter(
        GreetingCache.user_id == current_user.id,
        GreetingCache.generated_on == heute,
    ).one_or_none()
    if stored is not None and greeting_cache_is_fresh(stored.generated_on, heute) and stored.language == language:
        return _read(stored.sentence, stored.source, stored.model, stored.generated_at)

    data: DashboardResponse = serialize_dashboard(
        db, current_user.id, settings, now=now, user_timezone=current_user.timezone
    )
    outcome = render_greeting(
        db,
        current_user.id,
        settings,
        data,
        now=now,
        user_timezone=current_user.timezone,
        # Leere Liste ohne Freigabe - dann steht im Prompt keine Note.
        grade_lines=grades_service.grade_context_lines(db, current_user.id),
        language=language,
    )
    # Ein Satz des Tages in der anderen Sprache wird ersetzt, nicht ergaenzt:
    # die Zeile ist je Nutzer und Tag eindeutig.
    row = stored if stored is not None else GreetingCache(user_id=current_user.id, generated_on=heute)
    if stored is None:
        db.add(row)
    row.sentence = outcome.text
    row.source = outcome.source
    row.model = outcome.model
    row.language = language
    row.generated_on = heute
    row.generated_at = now
    db.commit()
    return _read(outcome.text, outcome.source, outcome.model, now)


@router.post("/refresh", response_model=GreetingRead)
def refresh_greeting(
    current_user: CurrentUser,
    db: DatabaseSession,
    settings: AppSettings,
    language: RequestLanguage,
) -> GreetingRead:
    """Erzeugt den Satz neu und ersetzt den heutigen Eintrag."""
    now = utc_now()
    zone = zone_or_utc(resolve_timezone_name(current_user.timezone or settings.timezone, settings.timezone))
    heute = now.astimezone(zone).date()

    data: DashboardResponse = serialize_dashboard(
        db, current_user.id, settings, now=now, user_timezone=current_user.timezone
    )
    outcome = render_greeting(
        db,
        current_user.id,
        settings,
        data,
        now=now,
        user_timezone=current_user.timezone,
        force=True,
        grade_lines=grades_service.grade_context_lines(db, current_user.id),
        language=language,
    )

    row = db.query(GreetingCache).filter(
        GreetingCache.user_id == current_user.id,
        GreetingCache.generated_on == heute,
    ).one_or_none()
    if row is None:
        row = GreetingCache(user_id=current_user.id, generated_on=heute)
        db.add(row)
    row.sentence = outcome.text
    row.source = outcome.source
    row.model = outcome.model
    row.language = language
    row.generated_at = now
    db.commit()
    return _read(outcome.text, outcome.source, outcome.model, now)

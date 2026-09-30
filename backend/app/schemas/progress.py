from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import UTCResponseModel


class TopicProgressWrite(BaseModel):
    """Der Lernstand eines Themas, wie ihn die Lernende selbst einschätzt.

    ``confidence`` ist der einzige Pflichtwert und liegt zwischen 0 und 1. Weil
    es eine Fließkommazahl ist, zeigt die Oberfläche die fünf Stufen aus
    ``CONFIDENCE_STEPS`` an - die Backend-Tabelle ist damit die einzige Quelle
    für die Bezeichnungen, und beide Enden können nicht auseinanderlaufen.

    ``last_reviewed_at`` hat drei Zustände, absichtlich:

    * **nicht gesetzt** - der Server trägt *jetzt* ein. Eine Einschätzung ohne
      Datum ist eine Wiederholung in diesem Moment; ein Intervall ohne Anker
      waere nicht berechenbar.
    * **explizit ``null``** - der Anker wird geloescht. Das Thema gilt dann
      wieder als nie wiederholt und es gibt keine geplante Wiederholung.
    * **Zeitpunkt** - er wird uebernommen, etwa wenn ein Thema nachgetragen wird.

    Ein ``notes`` mit Anweisung loescht die alte Notiz, ohne Anweisung bleibt
    sie stehen. Genau wie beim ``PATCH`` der Aufgaben: ein fehlendes Feld
    bedeutet "unveraendert".
    """

    model_config = ConfigDict(extra="forbid")

    confidence: float = Field(ge=0, le=1)
    # Der Zeitpunkt wird beim Schreiben ueber `as_utc` gehaertet, wie bei den
    # anderen Schreibschemas auch: ein naiver Wert bedeutet hier UTC.
    last_reviewed_at: datetime | None = None
    notes: str | None = Field(default=None, max_length=2000)


class WeeklyGoalWrite(BaseModel):
    """Das Wochenziel in gelernten Minuten. Kein Feld, das sich leeren laesst."""

    model_config = ConfigDict(extra="forbid")

    goal_minutes: int = Field(ge=15, le=20160)


class WeeklyGoalRead(UTCResponseModel):
    """Wochenziel und Ist-Stand derselben Woche.

    Die Woche laeuft **Montag bis Sonntag** in der Zeitzone des Nutzers -
    dieselbe Rechnung wie ``sync_window()`` beim Stundenplan und
    ``startOfWeek()`` im Frontend. ``days_remaining`` zaehlt die Tage der Woche,
    die noch kommen, heute eingeschlossen.
    """

    goal_minutes: int = Field(ge=0)
    studied_minutes: int = Field(ge=0)
    goal_reached: bool
    percent: float = Field(ge=0)
    days_studied: int = Field(ge=0)
    days_remaining: int = Field(ge=0)
    week_starts_on: date
    week_ends_on: date


class StreakRead(UTCResponseModel):
    """Die Serie aufeinanderfolgender Lerntage.

    * Ein **Lerntag** ist ein Kalendertag in der Zeitzone des Nutzers, an dem
      zusammen mindestens ``MIN_DAY_MINUTES`` Minuten auf abgeschlossene
      Lernsessions entfallen. Ein Tag zaehlt also erst, wenn wirklich gelernt
      wurde - ein verseentlich gestarteter und sofort gestoppter Fokusblock
      erzeugt keine Serie.
    * ``current_days`` laeuft von heute rueckwaerts. **Heute ist noch offen**:
      wer morgens nachsieht und gestern gelernt hat, verliert die Serie nicht,
      bevor er heute angefangen hat. Erst wenn gestern *und* heute nichts
      gelernt wurde, ist sie bei null.
    * ``longest_days`` ist die laengste Serie im Fenster von
      ``window_days`` Tagen, nicht ueber die gesamte Historie.
    """

    current_days: int = Field(ge=0)
    longest_days: int = Field(ge=0)
    studied_today: bool
    last_studied_on: date | None = None
    days_since_last: int | None = Field(default=None, ge=0)
    window_days: int = Field(ge=1)


class DayTotal(UTCResponseModel):
    """Ein einzelner Kalendertag in der Zeitzone des Nutzers."""

    date: date
    minutes: int = Field(ge=0)
    sessions: int = Field(ge=0)


class SubjectStudyTime(UTCResponseModel):
    """Lernzeit je Fach ueber die gesamte Historie.

    ``subject_id`` ist ``None`` fuer Lernsessions, deren Aufgabe an keinem
    Thema und damit an keinem Fach haengt. Die sind nicht weggezaehlt, sondern
    landen sichtbar in ``"Ohne Fach"``.
    """

    subject_id: int | None = None
    subject_name: str
    minutes: int = Field(ge=0)
    sessions: int = Field(ge=0)
    share_percent: float = Field(ge=0, le=100)
    last_studied_at: datetime | None = None


class WeekdayStudyTime(UTCResponseModel):
    """Lernzeit je Wochentag. ``weekday`` zaehlt ISO: 0 = Montag, 6 = Sonntag."""

    weekday: int = Field(ge=0, le=6)
    label: str
    minutes: int = Field(ge=0)
    sessions: int = Field(ge=0)


class ConfidenceStep(UTCResponseModel):
    """Eine Konfidenzstufe als Angebot fuer die Oberflaeche.

    Die Liste geht mit jeder Antwort mit, damit das Auswahlfeld nicht eine
    eigene Tabelle pflegen muss. Sie ist der Vertrag zwischen beiden Enden:
    aendert sich eine Stufe im Backend, aendert sich die Auswahl mit - und die
    Erklaerung unter der Liste ebenfalls.
    """

    value: float = Field(ge=0, le=1)
    label: str
    interval_days: int = Field(ge=1)


class TopicProgressRead(UTCResponseModel):
    """Der Lernstand eines Themas samt Wiederholungsvorschlag.

    ``confidence_label`` und ``interval_days`` kommen aus derselben Stufenliste
    wie die Auswahl in der Oberflaeche. ``interval_days`` ist ``None``, solange
    das Thema nie wiederholt wurde - ohne ``last_reviewed_at`` gibt es keinen
    Anker und damit keinen Termin.

    ``confidence_step`` ist die untere Grenze der Stufe, in die die Konfidenz
    faellt. Es ist nicht dasselbe wie ``confidence``: der Generator oder ein
    Aufrufer per API darf einen Wert zwischen zwei Stufen setzen, und das
    Auswahlfeld braucht trotzdem einen Wert, den es anwaehlen kann. Angezeigt
    wird deshalb die Stufe, denn die bestimmt das Intervall.

    ``studied_minutes`` und ``session_count`` stammen aus den Lernsessions der
    Aufgaben dieses Themas, nicht aus der Konfidenz: gelernte Zeit und
    eingeschaetzter Lernstand sind zwei verschiedene Groessen.
    """

    topic_id: int
    name: str
    subject_id: int | None = None
    subject_name: str | None = None
    confidence: float = Field(ge=0, le=1)
    confidence_step: float = Field(ge=0, le=1)
    confidence_label: str
    last_reviewed_at: datetime | None = None
    notes: str | None = None
    interval_days: int | None = None
    next_review_on: date | None = None
    days_until_review: int | None = None
    is_due: bool
    studied_minutes: int = Field(ge=0)
    session_count: int = Field(ge=0)
    updated_at: datetime


class LearningInsights(UTCResponseModel):
    """Das Lernprotokoll als Auswertung, nicht als Liste.

    Alles hier ist aus ``StudySession`` abgeleitet - mit zwei Ausnahmen:
    ``confidence_steps`` ist die Stufentabelle fuer das Auswahlfeld, und das
    laufende Protokoll geht **nicht** in die Summen ein, sondern steht in
    ``open_sessions`` und ``active_minutes``. Eine offene Session hat noch keine
    Dauer; waere sie schon in den Summen, wuerde sie beim Beenden ein zweites
    Mal gezaehlt.
    """

    generated_at: datetime
    timezone: str
    today: DayTotal
    week: WeeklyGoalRead
    streak: StreakRead
    by_subject: list[SubjectStudyTime]
    by_weekday: list[WeekdayStudyTime]
    confidence_steps: list[ConfidenceStep] = Field(default_factory=list)
    total_minutes: int = Field(ge=0)
    total_sessions: int = Field(ge=0)
    open_sessions: int = Field(ge=0)
    active_minutes: int = Field(ge=0)
    weekday_window_days: int = Field(ge=1)

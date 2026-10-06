"""Wie die Person lernt: Lernprofil fuer den Prompt und Abfragemodus aus dem eigenen Material.

* **Lernprofil** (:func:`profile_lines`): wenige Zeilen aus Daten, die es schon
  gibt - unsichere und faellige Themen, Lernzeiten, Abfrageergebnisse, eigene
  Aufschriebe je Fach. Sie stehen bei jeder Frage im Prompt und sind auf
  ``/assistent`` sichtbar; erfunden oder geschaetzt wird nichts.
* **Abfrage** (:func:`ask_question`, :func:`answer_question`): ein Absatz aus
  Aufschrieb, Arbeitsblatt oder Loesung wird zur Frage, die Antwort wird gegen
  genau diesen Absatz bewertet. Das Ergebnis aendert den Lernstand des Themas -
  so beantwortet die Abfrage die Frage "was kann ich schon?" mit Tatsachen.
* **Ohne Modell geht es trotzdem**: dann wird aus einem Satz eine Lueckenfrage,
  und bewertet wird ueber die Begriffe der Musterloesung. Weniger klug, aber
  ehrlich - und nie haengt die Abfrage an einem Modell.
"""

from __future__ import annotations

import random
import re
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.time import as_utc, utc_now
from app.db.entities import Document, DocumentChunk, LearningProgress, Subject, Topic
from app.db.quiz import Quiz, QuizAnswer, QuizAttempt, QuizQuestion
from app.services import ai_routing
from app.services.progress import _session_slices, confidence_label, next_review_on

QUIZ_SOURCE: Final[str] = "assistant"
QUIZ_KINDS: Final[tuple[str, ...]] = ("aufschrieb", "arbeitsblatt", "loesung")
MIN_CHUNK_CHARS: Final[int] = 120
#: So viele zuletzt gefragte Absaetze kommen nicht gleich wieder dran.
RECENT_QUESTIONS: Final[int] = 20
PROFILE_TOPICS: Final[int] = 4
PROFILE_SESSION_DAYS: Final[int] = 84
VERDICTS: Final[tuple[str, ...]] = ("richtig", "teilweise", "falsch")
CONFIDENCE_DELTA: Final[dict[str, float]] = {"richtig": 0.15, "teilweise": 0.05, "falsch": -0.15}
SCORES: Final[dict[str, float]] = {"richtig": 1.0, "teilweise": 0.5, "falsch": 0.0}

COACH_SYSTEM: Final[str] = (
    "Du bist ein freundlicher Lerncoach und fragst eine Schuelerin oder einen Schueler zu ihrem eigenen "
    "Schulmaterial ab. Du antwortest nur im verlangten Format und nur mit Inhalten aus dem Material."
)

_WORD_RE = re.compile(r"\w{4,}", re.UNICODE)
_QUESTION_RE = re.compile(r"FRAGE\s*:\s*(.+?)\s*(?:\n|$)", re.IGNORECASE)
_ANSWER_RE = re.compile(r"ANTWORT\s*:\s*(.+)", re.IGNORECASE | re.DOTALL)
_VERDICT_RE = re.compile(r"BEWERTUNG\s*:\s*\**\s*(richtig|teilweise|falsch)", re.IGNORECASE)
_EXPLANATION_RE = re.compile(r"ERKL(?:Ä|AE|A)RUNG\s*:\s*(.+)", re.IGNORECASE | re.DOTALL)

#: (System, Nutzer, max. Token) -> (Text, Herkunft) oder ``None``, wenn kein Weg antwortet.
Generator = Callable[[str, str, int], tuple[str, str] | None]


def routes_generator(db: Session, user_id: int, settings: Settings) -> Generator:
    """Der normale KI-Weg der Person (lokal zuerst) als Generator fuer die Abfrage."""
    routes = ai_routing.resolve_ai_routes(db, user_id, settings)

    def generate(system_prompt: str, user_prompt: str, max_tokens: int) -> tuple[str, str] | None:
        if not routes:
            return None
        outcome = ai_routing.generate_with_routes(
            routes,
            system_prompt,
            user_prompt,
            max_tokens=max_tokens,
            local_timeout=settings.ai_local_timeout_seconds,
            local_fast_timeout=settings.ai_local_fast_timeout_seconds,
            provider_timeout=settings.ai_generate_timeout_seconds,
        )
        if outcome is None or not outcome.text.strip():
            return None
        return outcome.text, outcome.source

    return generate


class QuizError(ValueError):
    """Abfrage geht gerade nicht (kein Material). Deutsche Meldung."""


# --- Lernprofil --------------------------------------------------------------


def _subject_name(subject: Subject | None) -> str:
    if subject is None:
        return "ohne Fach"
    return subject.short_name or subject.name


def _part_of_day(hour: int) -> str:
    if hour < 12:
        return "vormittags"
    if hour < 17:
        return "nachmittags"
    return "abends"


def profile_lines(db: Session, user_id: int, zone: ZoneInfo, now: datetime | None = None) -> list[str]:
    """Das Lernprofil als kurze Zeilen. Leer, wenn es noch nichts zu sagen gibt."""
    now = now or utc_now()
    today = as_utc(now).astimezone(zone).date()
    lines: list[str] = []

    rows = [
        (progress, topic, subject)
        for progress, topic, subject in db.execute(
            select(LearningProgress, Topic, Subject)
            .join(Topic, Topic.id == LearningProgress.topic_id)
            .outerjoin(Subject, Subject.id == Topic.subject_id)
            .where(LearningProgress.user_id == user_id, Topic.user_id == user_id)
        ).tuples()
    ]
    unsure = sorted((row for row in rows if row[0].confidence < 0.4), key=lambda row: row[0].confidence)
    due = [
        row
        for row in rows
        if (review := next_review_on(row[0].last_reviewed_at, row[0].confidence, zone)) is not None and review <= today
    ]
    sure = [row for row in rows if row[0].confidence >= 0.8]

    def names(selection: Sequence[tuple[LearningProgress, Topic, Subject | None]]) -> str:
        return ", ".join(f"{topic.name} ({_subject_name(subject)})" for _, topic, subject in selection[:PROFILE_TOPICS])

    if unsure:
        lines.append(f"Unsicher bei: {names(unsure)}.")
    if due:
        lines.append(f"Zur Wiederholung fällig: {names(due)}.")
    if sure:
        lines.append(f"Sicher bei: {names(sure)}.")

    slices = _session_slices(db, user_id, now - timedelta(days=PROFILE_SESSION_DAYS))
    durations = sorted(minutes for _, _, minutes in slices if minutes)
    if len(durations) >= 3:
        parts = Counter(_part_of_day(as_utc(start).astimezone(zone).hour) for start, _, _ in slices)
        median = durations[len(durations) // 2]
        lines.append(
            f"Lernt meist {parts.most_common(1)[0][0]}, typisch {median} Minuten am Stück "
            f"({len(slices)} Lerneinheiten in {PROFILE_SESSION_DAYS // 7} Wochen)."
        )

    answers = db.execute(
        select(QuizAnswer.is_correct, Subject)
        .join(QuizQuestion, QuizQuestion.id == QuizAnswer.question_id)
        .join(Quiz, Quiz.id == QuizQuestion.quiz_id)
        .outerjoin(Subject, Subject.id == Quiz.subject_id)
        .where(QuizAnswer.user_id == user_id, Quiz.source == QUIZ_SOURCE)
        .order_by(QuizAnswer.id.desc())
        .limit(40)
    ).tuples()
    totals: dict[str, list[int]] = {}
    for correct, subject in answers:
        entry = totals.setdefault(_subject_name(subject), [0, 0])
        entry[0] += 1 if correct else 0
        entry[1] += 1
    if totals:
        summary = ", ".join(f"{name} {right} von {count} richtig" for name, (right, count) in totals.items())
        lines.append(f"Abfragen zuletzt: {summary}.")

    notes = db.execute(
        select(Subject, func.count(Document.id))
        .select_from(Document)
        .outerjoin(Subject, Subject.id == Document.subject_id)
        .where(Document.user_id == user_id, Document.material_kind == "aufschrieb")
        .group_by(Subject.id)
    ).tuples()
    counted = ", ".join(f"{_subject_name(subject)} {count}" for subject, count in notes)
    if counted:
        lines.append(f"Eigene Aufschriebe: {counted}.")
    return lines


# --- Abfrage -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class QuizQuestionOut:
    question_id: int
    question: str
    document_id: int
    filename: str
    source: str


@dataclass(frozen=True, slots=True)
class QuizResult:
    verdict: str
    explanation: str
    reference: str
    excerpt: str
    filename: str
    source: str
    topic: str | None
    confidence_label: str | None


def _words(text: str) -> set[str]:
    return {word.casefold() for word in _WORD_RE.findall(text)}


def cloze(content: str, rng: random.Random) -> tuple[str, str] | None:
    """Lueckenfrage aus einem Satz: das laengste Fachwort fehlt."""
    sentences = [s.strip(" -*#\t") for s in re.split(r"(?<=[.!?])\s+|\n", content)]
    candidates = [s for s in sentences if 30 <= len(s) <= 220 and any(len(w) >= 6 for w in _WORD_RE.findall(s))]
    if not candidates:
        return None
    sentence = rng.choice(candidates)
    word = max(_WORD_RE.findall(sentence), key=len)
    return f"Ergänze die Lücke: {sentence.replace(word, '_____', 1)}", word


def grade_by_words(reference: str, answer: str) -> str:
    """Bewertung ohne Modell: wie viele Begriffe der Musterloesung stehen in der Antwort?"""
    expected = _words(reference) or {reference.strip().casefold()}
    given = _words(answer) | {answer.strip().casefold()}
    share = len(expected & given) / len(expected)
    if share >= 0.6:
        return "richtig"
    return "teilweise" if share >= 0.3 else "falsch"


def _recent_chunk_ids(db: Session, user_id: int) -> set[int]:
    rows = db.scalars(
        select(QuizQuestion.correct_answer)
        .join(Quiz, Quiz.id == QuizQuestion.quiz_id)
        .where(QuizQuestion.user_id == user_id, Quiz.source == QUIZ_SOURCE)
        .order_by(QuizQuestion.id.desc())
        .limit(RECENT_QUESTIONS)
    ).all()
    return {int(row["chunk_id"]) for row in rows if isinstance(row, dict) and row.get("chunk_id") is not None}


def _pick_chunk(
    db: Session, user_id: int, subject_id: int | None, document_id: int | None, rng: random.Random
) -> tuple[DocumentChunk, Document]:
    query = (
        select(DocumentChunk, Document)
        .join(Document, Document.id == DocumentChunk.document_id)
        .where(DocumentChunk.user_id == user_id, Document.user_id == user_id, Document.status == "indexed")
    )
    if subject_id is not None:
        query = query.where(Document.subject_id == subject_id)
    if document_id is not None:
        query = query.where(Document.id == document_id)
    rows = [(chunk, doc) for chunk, doc in db.execute(query).tuples() if len(chunk.content) >= MIN_CHUNK_CHARS]
    if not rows:
        raise QuizError("Dazu gibt es noch kein lesbares Material. Lade Aufschriebe oder Arbeitsblätter hoch.")
    preferred = [row for row in rows if row[1].material_kind in QUIZ_KINDS] or rows
    recent = _recent_chunk_ids(db, user_id)
    fresh = [row for row in preferred if row[0].id not in recent] or preferred
    return rng.choice(fresh)


def _quiz_for(db: Session, user_id: int, subject: Subject | None) -> Quiz:
    """Eine Abfrage-Sammlung je Fach - darin haengen alle Fragen und Versuche."""
    same_subject = Quiz.subject_id == subject.id if subject is not None else Quiz.subject_id.is_(None)
    quiz = db.scalar(select(Quiz).where(Quiz.user_id == user_id, Quiz.source == QUIZ_SOURCE, same_subject))
    if quiz is None:
        quiz = Quiz(
            user_id=user_id,
            subject_id=subject.id if subject is not None else None,
            title=f"Abfrage {_subject_name(subject)}",
            source=QUIZ_SOURCE,
        )
        db.add(quiz)
        db.flush()
    return quiz


def ask_question(
    db: Session,
    user_id: int,
    generate: Generator,
    *,
    subject_id: int | None = None,
    document_id: int | None = None,
    rng: random.Random | None = None,
) -> QuizQuestionOut:
    """Waehlt einen Absatz und macht daraus eine Frage mit Musterloesung."""
    rng = rng or random.Random()
    chunk, document = _pick_chunk(db, user_id, subject_id, document_id, rng)
    question: str | None = None
    reference: str | None = None
    source = "fallback"
    generated = generate(
        COACH_SYSTEM,
        f"Material ({document.filename}):\n{chunk.content}\n\n"
        "Stelle genau eine Frage, die prueft, ob man den Inhalt verstanden hat - nicht nur abgeschrieben. "
        "Die Antwort muss im Material stehen. Antworte genau so:\n"
        "FRAGE: <die Frage>\nANTWORT: <kurze Musterloesung, nur aus dem Material>",
        300,
    )
    if generated is not None:
        text, source = generated
        found_question, found_answer = _QUESTION_RE.search(text), _ANSWER_RE.search(text)
        if found_question and found_answer:
            question, reference = found_question.group(1).strip(), found_answer.group(1).strip()
    if not question or not reference:
        made = cloze(chunk.content, rng)
        if made is None:
            raise QuizError("Aus diesem Material ließ sich keine Frage bilden. Versuch es noch einmal.")
        question, reference = made
        source = "fallback"
    quiz = _quiz_for(db, user_id, document.subject)
    position = db.scalar(select(func.count()).select_from(QuizQuestion).where(QuizQuestion.quiz_id == quiz.id)) or 0
    row = QuizQuestion(
        user_id=user_id,
        quiz_id=quiz.id,
        prompt=question,
        options=[],
        correct_answer={"antwort": reference, "chunk_id": chunk.id, "document_id": document.id, "source": source},
        position=position,
    )
    db.add(row)
    db.commit()
    return QuizQuestionOut(
        question_id=row.id, question=question, document_id=document.id, filename=document.filename, source=source
    )


def _topic_for(db: Session, document: Document) -> Topic | None:
    """Das Thema des Dokuments: verknuepft, sonst gleicher Name im selben Fach. Angelegt wird keins."""
    if document.topics:
        return document.topics[0]
    if document.subject_id is None or not document.material_topic:
        return None
    return db.scalar(
        select(Topic).where(
            Topic.user_id == document.user_id,
            Topic.subject_id == document.subject_id,
            func.lower(Topic.name) == document.material_topic.lower(),
        )
    )


def answer_question(db: Session, user_id: int, question_id: int, answer: str, generate: Generator) -> QuizResult:
    """Bewertet die Antwort gegen Musterloesung und Absatz, haelt sie fest und passt den Lernstand an."""
    question = db.get(QuizQuestion, question_id)
    if question is None or question.user_id != user_id or question.quiz.source != QUIZ_SOURCE:
        raise LookupError(question_id)
    data = question.correct_answer if isinstance(question.correct_answer, dict) else {}
    reference = str(data.get("antwort", ""))
    chunk = db.get(DocumentChunk, data["chunk_id"]) if data.get("chunk_id") else None
    document = db.get(Document, data["document_id"]) if data.get("document_id") else None
    excerpt = chunk.content if chunk is not None and chunk.user_id == user_id else ""

    verdict: str | None = None
    explanation = ""
    source = "fallback"
    generated = generate(
        COACH_SYSTEM,
        f"Material:\n{excerpt}\n\nFrage: {question.prompt}\nMusterloesung: {reference}\n"
        f"Antwort der Schuelerin oder des Schuelers: {answer}\n\n"
        "Bewerte nur den Inhalt, Rechtschreibung zaehlt nicht. Antworte genau so:\n"
        "BEWERTUNG: richtig | teilweise | falsch\n"
        "ERKLAERUNG: <ein bis drei Saetze, freundlich, mit den Begriffen aus dem Material>",
        250,
    )
    if generated is not None:
        text, source = generated
        found_verdict, found_explanation = _VERDICT_RE.search(text), _EXPLANATION_RE.search(text)
        if found_verdict:
            verdict = found_verdict.group(1).lower()
            explanation = found_explanation.group(1).strip() if found_explanation else ""
    if verdict is None:
        verdict = grade_by_words(reference, answer)
        source = "fallback"
    if not explanation:
        explanation = "Genau so steht es in deinem Material." if verdict == "richtig" else f"Musterlösung: {reference}"

    moment = utc_now()
    attempt = QuizAttempt(
        user_id=user_id, quiz_id=question.quiz_id, score=SCORES[verdict], started_at=moment, completed_at=moment
    )
    db.add(attempt)
    db.flush()
    db.add(
        QuizAnswer(
            user_id=user_id,
            attempt_id=attempt.id,
            question_id=question.id,
            answer={"text": answer, "verdict": verdict, "source": source},
            is_correct=verdict == "richtig",
        )
    )

    topic = _topic_for(db, document) if document is not None and document.user_id == user_id else None
    label: str | None = None
    if topic is not None:
        progress = db.get(LearningProgress, (user_id, topic.id))
        if progress is None:
            progress = LearningProgress(user_id=user_id, topic_id=topic.id, confidence=0.0)
            db.add(progress)
        progress.confidence = min(1.0, max(0.0, progress.confidence + CONFIDENCE_DELTA[verdict]))
        progress.last_reviewed_at = utc_now()
        label = confidence_label(progress.confidence)
    db.commit()
    return QuizResult(
        verdict=verdict,
        explanation=explanation,
        reference=reference,
        excerpt=excerpt,
        filename=document.filename if document is not None else "",
        source=source,
        topic=topic.name if topic is not None else None,
        confidence_label=label,
    )

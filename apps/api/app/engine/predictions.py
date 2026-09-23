from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, select

from app.db.models import PredictionOutcome, PredictionRecord, TickRecord
from app.db.session import SessionLocal
from app.models.market import BrainAnalysis


async def persist_prediction(analysis: BrainAnalysis) -> int | None:
    if analysis.price is None:
        return None
    now = analysis.timestamp.astimezone(timezone.utc).replace(tzinfo=None)
    minute = now.replace(second=0, microsecond=0)
    with SessionLocal() as session:
        existing = session.scalar(
            select(PredictionRecord.id).where(
                PredictionRecord.symbol == analysis.symbol,
                PredictionRecord.timestamp >= minute,
                PredictionRecord.timestamp < minute + timedelta(minutes=1),
            )
        )
        if existing is not None:
            return existing
        record = PredictionRecord(
            symbol=analysis.symbol,
            timestamp=now,
            decision=analysis.decision,
            confidence=analysis.confidence,
            price=analysis.price,
            regime=analysis.regime,
            risk=analysis.risk,
            score=analysis.score,
            reasons_json=json.dumps(analysis.reasons_for),
            against_json=json.dumps(analysis.reasons_against),
            snapshot_json=analysis.model_dump_json(),
            brain_version=analysis.brain_version,
        )
        session.add(record)
        session.commit()
        session.refresh(record)
        return record.id


async def evaluate_outcomes() -> int:
    horizons = (15, 60, 240)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    evaluated = 0
    with SessionLocal() as session:
        predictions = session.scalars(
            select(PredictionRecord)
            .where(PredictionRecord.timestamp <= now - timedelta(minutes=15))
            .order_by(PredictionRecord.timestamp.desc())
            .limit(500)
        ).all()

        for pred in predictions:
            for horizon in horizons:
                target = pred.timestamp + timedelta(minutes=horizon)
                if target > now:
                    continue
                exists = session.scalar(
                    select(PredictionOutcome.id).where(
                        and_(
                            PredictionOutcome.prediction_id == pred.id,
                            PredictionOutcome.horizon_minutes == horizon,
                        )
                    )
                )
                if exists is not None:
                    continue
                tick = session.scalars(
                    select(TickRecord)
                    .where(
                        TickRecord.symbol == pred.symbol,
                        TickRecord.market_timestamp >= target - timedelta(minutes=2),
                        TickRecord.market_timestamp <= target + timedelta(minutes=2),
                    )
                    .order_by(TickRecord.market_timestamp.asc())
                    .limit(1)
                ).first()
                if tick is None:
                    continue
                change = float(tick.price) - float(pred.price)
                correct = None
                if pred.decision == "BUY":
                    correct = change > 0
                elif pred.decision == "SELL":
                    correct = change < 0
                session.add(
                    PredictionOutcome(
                        prediction_id=pred.id,
                        horizon_minutes=horizon,
                        evaluated_at=now,
                        future_price=float(tick.price),
                        price_change=change,
                        direction_correct=correct,
                    )
                )
                evaluated += 1
        session.commit()
    return evaluated


async def performance_summary() -> dict:
    with SessionLocal() as session:
        predictions = session.scalars(select(PredictionRecord)).all()
        outcomes = session.scalars(select(PredictionOutcome)).all()

    directional = [p for p in predictions if p.decision in {"BUY", "SELL"}]
    sixty = [o for o in outcomes if o.horizon_minutes == 60 and o.direction_correct is not None]
    correct = [o for o in sixty if o.direction_correct]

    return {
        "total_predictions": len(predictions),
        "directional_predictions": len(directional),
        "wait_decisions": len([p for p in predictions if p.decision == "WAIT"]),
        "no_decisions": len([p for p in predictions if p.decision == "NO_DECISION"]),
        "evaluated_60m": len(sixty),
        "direction_accuracy_60m": round(len(correct) / len(sixty) * 100, 1) if sixty else None,
        "brain_version": "rules-v0.1",
        "note": "Statistics describe this installation's recorded observations; they are not a guarantee of future results.",
    }


async def recent_predictions(limit: int = 50) -> list[dict]:
    with SessionLocal() as session:
        rows = session.scalars(
            select(PredictionRecord).order_by(PredictionRecord.timestamp.desc()).limit(limit)
        ).all()
    return [
        {
            "id": r.id,
            "timestamp": r.timestamp.isoformat(),
            "decision": r.decision,
            "confidence": r.confidence,
            "price": r.price,
            "regime": r.regime,
            "risk": r.risk,
            "score": r.score,
            "brain_version": r.brain_version,
        }
        for r in rows
    ]

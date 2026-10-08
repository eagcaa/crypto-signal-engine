from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from crypto_signal_engine.db.models import PaperPositionRow
from crypto_signal_engine.paper.models import PaperPosition, PaperPositionStatus


class PaperPositionRepository:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._session_factory = session_factory

    async def add(self, position: PaperPosition) -> None:
        async with self._session_factory() as session:
            session.add(
                PaperPositionRow(
                    id=position.id,
                    prediction_id=position.prediction_id,
                    symbol=position.symbol,
                    horizon_seconds=position.horizon_seconds,
                    direction=position.direction,
                    opened_at=position.opened_at,
                    entry_price=position.entry_price,
                    notional=position.notional,
                    quantity=position.quantity,
                    model_name=position.model_name,
                    status=position.status.value,
                    closed_at=position.closed_at,
                    exit_price=position.exit_price,
                    return_pct=position.return_pct,
                    pnl=position.pnl,
                    close_reason=position.close_reason,
                )
            )
            await session.commit()

    async def update(self, position: PaperPosition) -> None:
        async with self._session_factory() as session:
            statement = (
                update(PaperPositionRow)
                .where(
                    PaperPositionRow.prediction_id
                    == position.prediction_id
                )
                .values(
                    status=position.status.value,
                    closed_at=position.closed_at,
                    exit_price=position.exit_price,
                    return_pct=position.return_pct,
                    pnl=position.pnl,
                    close_reason=position.close_reason,
                )
            )
            await session.execute(statement)
            await session.commit()

    async def load_all(
        self,
        *,
        model_names: tuple[str, ...] | None = None,
    ) -> list[PaperPosition]:
        async with self._session_factory() as session:
            query = select(PaperPositionRow)

            if model_names:
                query = query.where(
                    PaperPositionRow.model_name.in_(model_names)
                )

            query = query.order_by(
                PaperPositionRow.opened_at.asc()
            )
            rows = list((await session.scalars(query)).all())
            return [self._to_position(row) for row in rows]

    @staticmethod
    def _to_position(row: PaperPositionRow) -> PaperPosition:
        return PaperPosition(
            id=row.id,
            prediction_id=row.prediction_id,
            symbol=row.symbol,
            horizon_seconds=row.horizon_seconds,
            direction=row.direction,
            opened_at=row.opened_at,
            entry_price=row.entry_price,
            notional=row.notional,
            quantity=row.quantity,
            model_name=row.model_name,
            status=PaperPositionStatus(row.status),
            closed_at=row.closed_at,
            exit_price=row.exit_price,
            return_pct=row.return_pct,
            pnl=row.pnl,
            close_reason=row.close_reason,
        )

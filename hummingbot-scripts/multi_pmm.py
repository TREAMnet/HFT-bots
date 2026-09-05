import logging
import os
import re
from decimal import Decimal
from typing import Dict, List

from pydantic import Field, field_validator, model_validator

from hummingbot.connector.connector_base import ConnectorBase
from hummingbot.core.data_type.common import MarketDict, OrderType, PriceType, TradeType
from hummingbot.core.data_type.order_candidate import OrderCandidate
from hummingbot.core.event.events import OrderFilledEvent
from hummingbot.strategy.strategy_v2_base import StrategyV2Base, StrategyV2ConfigBase

PAIR_RE = re.compile(r"^[A-Z0-9]+-[A-Z0-9]+$")


class MultiPMMConfig(StrategyV2ConfigBase):
    script_file_name: str = os.path.basename(__file__)
    controllers_config: List[str] = []
    exchange: str = Field("binance_paper_trade")
    trading_pairs: List[str] = Field(default_factory=lambda: ["BTC-USDT"])
    order_amount: Dict[str, Decimal] = Field(default_factory=lambda: {"BTC-USDT": Decimal("0.01")})
    bid_spread: Decimal = Field(Decimal("0.001"))
    ask_spread: Decimal = Field(Decimal("0.001"))
    order_refresh_time: int = Field(15)
    price_type: str = Field("mid")

    @field_validator("trading_pairs")
    @classmethod
    def _validate_pairs(cls, v: List[str]) -> List[str]:
        if not v:
            raise ValueError("trading_pairs must not be empty")
        if len(set(v)) != len(v):
            raise ValueError("trading_pairs must not contain duplicates")
        for pair in v:
            if not PAIR_RE.match(pair):
                raise ValueError(f"invalid trading pair format: {pair!r} (expected e.g. BTC-USDT)")
        return v

    @field_validator("bid_spread", "ask_spread")
    @classmethod
    def _validate_spread(cls, v: Decimal) -> Decimal:
        if not (Decimal("0") < v < Decimal("0.5")):
            raise ValueError("spread must be between 0 and 0.5 (0%-50%)")
        return v

    @field_validator("order_amount")
    @classmethod
    def _validate_amount(cls, v: Dict[str, Decimal]) -> Dict[str, Decimal]:
        for pair, amount in v.items():
            if amount <= 0:
                raise ValueError(f"order_amount for {pair!r} must be positive")
        return v

    @model_validator(mode="after")
    def _validate_amount_covers_pairs(self) -> "MultiPMMConfig":
        missing = [p for p in self.trading_pairs if p not in self.order_amount]
        if missing:
            raise ValueError(f"order_amount is missing an entry for: {', '.join(missing)}")
        return self

    @field_validator("order_refresh_time")
    @classmethod
    def _validate_refresh(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("order_refresh_time must be positive")
        return v

    def update_markets(self, markets: MarketDict) -> MarketDict:
        markets[self.exchange] = markets.get(self.exchange, set()) | set(self.trading_pairs)
        return markets


class MultiPMM(StrategyV2Base):
    """
    Multi-pair variant of the bundled simple_pmm.py: places a buy/sell pair
    of limit orders around the mid/last price for EACH configured trading
    pair, refreshing all of them every order_refresh_time seconds. Same
    order-placement path as simple_pmm.py (buy()/sell()/OrderCandidate) —
    deliberately not using V2 controllers/PositionExecutor, which hit an
    unfixable paper-trade bug (see hummingbot-setup-spec.md Phase 1b).
    """

    create_timestamp = 0
    price_source = PriceType.MidPrice

    def __init__(self, connectors: Dict[str, ConnectorBase], config: MultiPMMConfig):
        super().__init__(connectors, config)
        self.config = config
        self.price_source = PriceType.LastTrade if self.config.price_type == "last" else PriceType.MidPrice

    def on_tick(self):
        if self.create_timestamp <= self.current_timestamp:
            self.cancel_all_orders()
            proposal: List[OrderCandidate] = self.create_proposal()
            proposal_adjusted: List[OrderCandidate] = self.adjust_proposal_to_budget(proposal)
            self.place_orders(proposal_adjusted)
            self.create_timestamp = self.config.order_refresh_time + self.current_timestamp

    def create_proposal(self) -> List[OrderCandidate]:
        orders: List[OrderCandidate] = []
        for trading_pair in self.config.trading_pairs:
            ref_price = self.connectors[self.config.exchange].get_price_by_type(trading_pair, self.price_source)
            buy_price = ref_price * Decimal(1 - self.config.bid_spread)
            sell_price = ref_price * Decimal(1 + self.config.ask_spread)
            amount = Decimal(self.config.order_amount[trading_pair])
            orders.append(OrderCandidate(trading_pair=trading_pair, is_maker=True, order_type=OrderType.LIMIT,
                                          order_side=TradeType.BUY, amount=amount, price=buy_price))
            orders.append(OrderCandidate(trading_pair=trading_pair, is_maker=True, order_type=OrderType.LIMIT,
                                          order_side=TradeType.SELL, amount=amount, price=sell_price))
        return orders

    def adjust_proposal_to_budget(self, proposal: List[OrderCandidate]) -> List[OrderCandidate]:
        return self.connectors[self.config.exchange].budget_checker.adjust_candidates(proposal, all_or_none=True)

    def place_orders(self, proposal: List[OrderCandidate]) -> None:
        for order in proposal:
            self.place_order(connector_name=self.config.exchange, order=order)

    def place_order(self, connector_name: str, order: OrderCandidate):
        if order.order_side == TradeType.SELL:
            self.sell(connector_name=connector_name, trading_pair=order.trading_pair, amount=order.amount,
                      order_type=order.order_type, price=order.price)
        elif order.order_side == TradeType.BUY:
            self.buy(connector_name=connector_name, trading_pair=order.trading_pair, amount=order.amount,
                     order_type=order.order_type, price=order.price)

    def cancel_all_orders(self):
        for order in self.get_active_orders(connector_name=self.config.exchange):
            self.cancel(self.config.exchange, order.trading_pair, order.client_order_id)

    def did_fill_order(self, event: OrderFilledEvent):
        msg = f"{event.trade_type.name} {round(event.amount, 2)} {event.trading_pair} {self.config.exchange} at {round(event.price, 2)}"
        self.log_with_clock(logging.INFO, msg)
        self.notify_hb_app_with_timestamp(msg)

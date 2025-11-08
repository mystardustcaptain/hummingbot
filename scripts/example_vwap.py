import math
from decimal import Decimal
from typing import Dict

from hummingbot.connector.utils import split_hb_trading_pair
from hummingbot.core.data_type.common import TradeType, OrderType
from hummingbot.core.data_type.order_candidate import OrderCandidate
from hummingbot.core.event.events import OrderFilledEvent
from hummingbot.core.rate_oracle.rate_oracle import RateOracle
from hummingbot.strategy.script_strategy_base import ScriptStrategyBase


class ExampleVWAP(ScriptStrategyBase):
    last_ordered_ts = 0

    vwap: Dict = {
        "connector_name": "kucoin_paper_trade",
        "trading_pair": "ETH-USDT",
        "is_buy": True,
        "total_volume_usd": 10000,
        "price_spread": 0.01,
        "volume_perc": 0.001,
        "order_delay_time": 10
    }

    markets = {vwap["connector_name"]: {vwap["trading_pair"]}}

    def on_tick(self):
        try:
            status = self.vwap.get("status")
            order_delay_time_ = self.vwap["order_delay_time"]

            # check time since last order
            # if insufficient time has passed, exit
            if self.last_ordered_ts + order_delay_time_ > self.current_timestamp:
                return

            # is VWAP status initialized?
            if status is None:
                self.init_vwap()
                return

            connector_name_ = self.vwap["connector_name"]
            connector = self.vwap["connector"]
            trading_pair_ = self.vwap["trading_pair"]
            is_buy_ = self.vwap["is_buy"]

            if status != "ACTIVE":
                return

            # Create order candidate
            order: OrderCandidate = self.create_order()

            # Adjust order based on budget
            order_adjusted = connector.budget_checker.adjust_candidate(order, all_or_none=False)

            # Check if adjusted order amount is too low or close to 1E-5
            if math.isclose(order_adjusted.amount, Decimal("0"), rel_tol=1E-5):
                self.logger().info(f"Order adjusted: {order_adjusted.amount}, too low to place an order")
                return

            # Place the order
            self.place_order(
                connector_name=connector_name_,
                trading_pair=trading_pair_,
                is_buy=is_buy_,
                amount=order_adjusted.amount,
                order_type=order_adjusted.order_type
            )

            # Update last ordered timestamp
            self.last_ordered_ts = self.current_timestamp
        except Exception as e:
            self.logger().error(f"Exception in on_tick: {e}", exc_info=True)

    def init_vwap(self):
        vwap = self.vwap.copy()

        connector = self.connectors[vwap["connector_name"]]
        vwap["connector"] = connector

        vwap["delta"] = 0 # 1 if completed
        vwap["status"] = "ACTIVE" # COMPLETE if done

        vwap["trades"] = []

        is_buy = self.vwap["is_buy"]
        vwap["trade_type"] = TradeType.BUY if is_buy else TradeType.SELL

        trading_pair = vwap["trading_pair"]
        vwap["start_price"] = connector.get_price(trading_pair, is_buy)

        # base_asset, quote_asset = split_hb_trading_pair(trading_pair)
        # base_conversion_trading_pair = f"{base_asset}-USD"
        # quote_conversion_trading_pair = f"{quote_asset}-USD"

        # base_conversion_rate = RateOracle.get_instance().get_pair_rate(base_conversion_trading_pair)
        # quote_conversion_rate = RateOracle.get_instance().get_pair_rate(quote_conversion_trading_pair)

        # # Matt: There seems to be a bug in the RateOracle where it does not return correct rates for pairs in -USD.
        # unless rate oracle source is CoinGecko.
        # # Using -USDT as a workaround.
        base_conversion_rate = RateOracle.get_instance().get_pair_rate(trading_pair)

        total_volume_usd = vwap["total_volume_usd"]
        vwap["target_base_volume"] = total_volume_usd / base_conversion_rate
        # vwap["ideal_quote_volume"] = total_volume_usd / quote_conversion_rate

        result = connector.get_quote_volume_for_base_amount(
            trading_pair,
            is_buy,
            vwap["target_base_volume"]
        )

        vwap['market_order_base_volume'] = result.query_volume
        vwap['market_order_quote_volume'] = result.result_volume

        vwap['volume_remaining'] = vwap['target_base_volume']
        vwap['real_quote_volume'] = Decimal(0)

        self.vwap = vwap

    def create_order(self) -> OrderCandidate:
        self.logger().info(f"====================== Creating VWAP order ======================")

        connector = self.vwap["connector"]
        trading_pair = self.vwap["trading_pair"]

        mid_price = float(connector.get_mid_price(trading_pair))
        self.logger().info(f"mid_price: {mid_price}")

        price_spread = self.vwap["price_spread"]
        is_buy = self.vwap["is_buy"]
        price_multiplier = 1 + price_spread if is_buy else 1 - price_spread
        price_target = mid_price * price_multiplier

        # Query cumulative volume until price target
        query = connector.get_volume_for_price(
            trading_pair=trading_pair,
            is_buy=is_buy,
            price=price_target
        )

        volume_for_price_target = query.result_volume
        self.logger().info(f"Volume available up to price target {price_target}: {volume_for_price_target}")

        # Choose the minimum between
        # volume for price target * volume perc and volume remaining
        volume_min = min(
            volume_for_price_target * Decimal(self.vwap["volume_perc"]),
            Decimal(self.vwap["volume_remaining"])
        )

        # Quantize volume and price
        volume_min_quantized = connector.quantize_order_amount(trading_pair, volume_min)
        price_target_quantized = connector.quantize_order_price(trading_pair, Decimal(price_target))

        # Create OrderCandidate
        order = OrderCandidate(
            trading_pair=trading_pair,
            is_maker=False,
            order_type=OrderType.MARKET,
            order_side=self.vwap["trade_type"],
            amount=volume_min_quantized,
            price=price_target_quantized
        )

        self.logger().info(f"Created VWAP order candidate: {order}")

        return order

    def place_order(self, connector_name, trading_pair, is_buy, amount, order_type, price=Decimal("NaN")):
        if is_buy:
            self.buy(connector_name, trading_pair, amount, order_type, price)
        else:
            self.sell(connector_name, trading_pair, amount, order_type, price)

        self.logger().info(f"Placed {'BUY' if is_buy else 'SELL'} order for {amount} {trading_pair} as part of VWAP strategy.")
        
    def did_fill_order(self, order_filled_event: OrderFilledEvent):
        trading_pair_ = self.vwap["trading_pair"]
        trade_type_ = self.vwap["trade_type"]
        target_base_volume_ = self.vwap["target_base_volume"]

        if trading_pair_ != order_filled_event.trading_pair or trade_type_ != order_filled_event.trade_type:
            return

        # Update Volume Remaining
        self.vwap["volume_remaining"] -= order_filled_event.amount
        remaining_ = self.vwap["volume_remaining"]

        # Update Delta / Progress
        self.vwap["delta"] = (target_base_volume_ - remaining_) / target_base_volume_
        delta_ = self.vwap["delta"]

        # Update Real Quote Volume
        self.vwap["real_quote_volume"] += order_filled_event.price * order_filled_event.amount

        # Update status if complete
        if math.isclose(delta_, 1, rel_tol=1e-5):
            self.vwap["status"] = "COMPLETE"

    def format_status(self) -> str:
        """
        Returns status of the current strategy on user balances and current active orders. This function is called
        when status command is issued. Override this function to create custom status display output.
        """
        if not self.ready_to_trade:
            return "Market connectors are not ready."

        lines = []
        warning_lines = []
        warning_lines.extend(self.network_warning(self.get_market_trading_pair_tuples()))

        balance_df = self.get_balance_df()
        lines.extend(["", "  Balances:"] + ["    " + line for line in balance_df.to_string(index=False).split("\n")])

        try:
            df = self.active_orders_df()
            lines.extend(["", "  Orders:"] + ["    " + line for line in df.to_string(index=False).split("\n")])
        except ValueError:
            lines.extend(["", "  No active maker orders."])

        # Add VWAP info (str)
        lines.extend(["", "  VWAP Info:"] + [" " + key + ": " + value
                                             for key, value in self.vwap.items()
                                            if isinstance(value, str)])
        # Add VWAP info (numeric)
        lines.extend(["", "  VWAP Stats:"] + [" " + key + ": " + str(round(value, 4))
                                             for key, value in self.vwap.items()
                                            if type(value) in [int, float, Decimal]])

        warning_lines.extend(self.balance_warning(self.get_market_trading_pair_tuples()))
        if len(warning_lines) > 0:
            lines.extend(["", "*** WARNINGS ***"] + warning_lines)
        return "\n".join(lines)


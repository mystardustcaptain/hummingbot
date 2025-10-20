from decimal import Decimal
from hummingbot.client.hummingbot_application import HummingbotApplication
from hummingbot.core.data_type.common import OrderType
from hummingbot.core.rate_oracle.rate_oracle import RateOracle
from hummingbot.strategy.script_strategy_base import ScriptStrategyBase


class Example_3(ScriptStrategyBase):
    order_amount_usd = 100.0
    orders_created = 0
    orders_to_create = 3
    base = "ETH"
    quote = "USDT"
    markets = {
        "kucoin_paper_trade": {f"{base}-{quote}"}
    }


    def on_tick(self):
        if self.orders_created < self.orders_to_create:
            self.logger().info(f"Placing buy order {self.orders_created + 1} of {self.orders_to_create}")

            conversion_rate = RateOracle.get_instance().get_pair_rate(f"{self.base}-{self.quote}")
            amount = Decimal(self.order_amount_usd) / conversion_rate

            mid_price = self.connectors["kucoin_paper_trade"].get_mid_price(f"{self.base}-{self.quote}")
            price = mid_price * Decimal(0.99)

            self.buy(
                connector_name="kucoin_paper_trade",
                trading_pair=f"{self.base}-{self.quote}",
                amount=amount,
                order_type=OrderType.LIMIT,
                price=price
            )

    def did_create_buy_order(self, order):
        if order.trading_pair == f"{self.base}-{self.quote}":
            self.orders_created += 1
            self.logger().info(f"Buy order {self.orders_created} of {self.orders_to_create} created.")
            if self.orders_created == self.orders_to_create:
                self.logger().info("All buy orders created. Stopping strategy.")
                HummingbotApplication.main_application().stop()
# PyAlgoTrade
#
# Copyright 2011-2018 Gabriel Martin Becedillas Ruiz
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
.. moduleauthor:: Gabriel Martin Becedillas Ruiz <gabriel.becedillas@gmail.com>
"""

import unittest
import datetime
import time
import threading

import pytest
from six.moves import queue

from . import common as tc_common
from . import test_strategy

from pyalgotrade import broker as basebroker
from pyalgotrade.bitstamp import barfeed
from pyalgotrade.bitstamp import broker
from pyalgotrade.bitstamp import wsclient
from pyalgotrade.bitstamp import httpclient
from pyalgotrade.bitstamp import livebroker
from pyalgotrade.bitcoincharts import barfeed as btcbarfeed
from pyalgotrade import strategy
from pyalgotrade import dispatcher
from pyalgotrade.utils import dt


SYMBOL = "BTC"
PRICE_CURRENCY = "USD"
INSTRUMENT = "BTC/USD"


class WebSocketClientThreadMock(threading.Thread):
    def __init__(self, events):
        threading.Thread.__init__(self)
        self.__queue = queue.Queue()
        for event in events:
            self.__queue.put(event)
        self.__queue.put((wsclient.WebSocketClient.Event.DISCONNECTED, None))
        self.__stop = False

    def waitInitialized(self, timeout):
        return True

    def getQueue(self):
        return self.__queue

    def start(self):
        threading.Thread.start(self)

    def run(self):
        while not self.__queue.empty() and not self.__stop:
            time.sleep(0.01)

    def stop(self):
        self.__stop = True


class TestingLiveTradeFeed(barfeed.LiveTradeFeed):
    def __init__(self):
        super(TestingLiveTradeFeed, self).__init__([INSTRUMENT])
        # Disable reconnections so the test finishes when ON_DISCONNECTED is pushed.
        self.enableReconection(False)
        self.__events = []
        self.__lastDateTime = None

    def addTrade(self, dateTime, tid, price, amount):
        # To avoid collisions.
        if dateTime == self.__lastDateTime:
            dateTime += datetime.timedelta(microseconds=len(self.__events))
        self.__lastDateTime = dateTime

        eventDict = {
            "data": {
                "id": tid,
                "price": price,
                "amount": amount,
                "microtimestamp": int(dt.datetime_to_timestamp(dateTime) * 1e6),
                "type": 0,
            },
            "channel": "live_trades_btcusd",
        }
        self.__events.append((wsclient.WebSocketClient.Event.TRADE, wsclient.Trade(eventDict)))

    def buildWebSocketClientThread(self):
        return WebSocketClientThreadMock(self.__events)


class HTTPClientMock(object):
    class UserTransactionType:
        MARKET_TRADE = 2

    def __init__(self):
        self.__userTransactions = []
        self.__openOrders = []
        self.__btcAvailable = 0.0
        self.__usdAvailable = 0.0
        self.__nextTxId = 1
        self.__nextOrderId = 1000
        self.__userTransactionsRequested = False

    def setUSDAvailable(self, usd):
        self.__usdAvailable = usd

    def setBTCAvailable(self, btc):
        self.__btcAvailable = btc

    def addOpenOrder(self, orderId, btcAmount, usdAmount):
        jsonDict = {
            'id': orderId,
            'datetime': str(datetime.datetime.now()),
            'type': 0 if btcAmount > 0 else 1,
            'price': str(usdAmount),
            'amount': str(abs(btcAmount)),
            'currency_pair': "BTC/USD",
        }
        self.__openOrders.append(jsonDict)

    def addUserTransaction(self, orderId, btcAmount, usdAmount, fillPrice, fee):
        jsonDict = {
            'btc': str(btcAmount),
            'btc_usd': str(fillPrice),
            'datetime': str(datetime.datetime.now()),
            'fee': str(fee),
            'id': self.__nextTxId,
            'order_id': orderId,
            'type': 2,
            'usd': str(usdAmount)
        }
        self.__userTransactions.insert(0, jsonDict)
        self.__nextTxId += 1

    def getAccountBalance(self):
        jsonDict = {
            'btc_available': str(self.__btcAvailable),
            # 'btc_balance': '0',
            # 'btc_reserved': '0',
            # 'fee': '0.5000',
            'usd_available': str(self.__usdAvailable),
            # 'usd_balance': '0.00',
            # 'usd_reserved': '0'
        }
        return httpclient.AccountBalance(jsonDict)

    def getOpenOrders(self):
        return [httpclient.Order(jsonDict) for jsonDict in self.__openOrders]

    def cancelOrder(self, orderId):
        pass

    def _buildOrder(self, price, amount):
        jsonDict = {
            'id': self.__nextOrderId,
            'datetime': str(datetime.datetime.now()),
            'type': 0 if amount > 0 else 1,
            'price': str(price),
            'amount': str(abs(amount)),
        }
        self.__nextOrderId += 1
        return httpclient.Order(jsonDict)

    def buyLimit(self, currencyPair, limitPrice, quantity):
        assert(quantity > 0)
        return self._buildOrder(limitPrice, quantity)

    def sellLimit(self, currencyPair, limitPrice, quantity):
        assert(quantity > 0)
        return self._buildOrder(limitPrice, quantity)

    def getUserTransactions(self, transactionType=None):
        # The first call is to retrieve user transactions that should have been
        # processed already.
        if not self.__userTransactionsRequested:
            self.__userTransactionsRequested = True
            return []
        else:
            return [httpclient.UserTransaction(jsonDict) for jsonDict in self.__userTransactions]


class TestingLiveBroker(broker.LiveBroker):
    def __init__(self, clientId, key, secret):
        self.__httpClient = HTTPClientMock()
        broker.LiveBroker.__init__(self, clientId, key, secret)

    def buildHTTPClient(self, clientId, key, secret):
        return self.__httpClient

    def getHTTPClient(self):
        return self.__httpClient


class NonceTest(unittest.TestCase):
    def testNonceGenerator(self):
        gen = httpclient.NonceGenerator()
        prevNonce = 0
        for i in range(1000):
            nonce = gen.getNext()
            self.assertGreater(nonce, prevNonce)
            prevNonce = nonce


class TestStrategy(test_strategy.BaseStrategy):
    def __init__(self, feed, brk):
        super(TestStrategy, self).__init__(feed, brk)
        self.bid = None
        self.ask = None

        # Subscribe to order book update events to get bid/ask prices to trade.
        feed.getOrderBookUpdateEvent().subscribe(self.__onOrderBookUpdate)

    def __onOrderBookUpdate(self, orderBookUpdate):
        bid = orderBookUpdate.getBidPrices()[0]
        ask = orderBookUpdate.getAskPrices()[0]

        if bid != self.bid or ask != self.ask:
            self.bid = bid
            self.ask = ask


@pytest.mark.parametrize("amount, symbol, expected", [
    (0, "USD", 0),
    (1, "USD", 1),
    (1.123, "USD", 1.12),
    (1.1 + 1.1 + 1.1, "USD", 3.3),
    (1.1 + 1.1 + 1.1 - 3.3, "USD", 0),
    (0.00441376, "BTC", 0.00441376),
    (0.004413764, "BTC", 0.00441376),
    (10.004413764123499, "ETH", 10.004413764123499),
])
def test_instrument_traits(amount, symbol, expected):
    traits = livebroker.InstrumentTraits()
    assert traits.round(amount, symbol) == expected


class BacktestingTestCase(tc_common.TestCase):
    def testBitcoinChartsFeed(self):

        class TestStrategy(strategy.BaseStrategy):
            def __init__(self, feed, brk):
                strategy.BaseStrategy.__init__(self, feed, brk)
                self.pos = None

            def onBars(self, bars):
                if not self.pos:
                    self.pos = self.enterLongLimit(INSTRUMENT, 5.83, 5, True)

        barFeed = btcbarfeed.CSVTradeFeed()
        barFeed.addBarsFromCSV(tc_common.get_data_file_path("bitstampUSD.csv"), instrument=INSTRUMENT)
        brk = broker.BacktestingBroker({PRICE_CURRENCY: 100}, barFeed)
        strat = TestStrategy(barFeed, brk)
        strat.run()
        self.assertEqual(strat.pos.getShares(), 5)
        self.assertEqual(strat.pos.entryActive(), False)
        self.assertEqual(strat.pos.isOpen(), True)
        self.assertEqual(
            strat.pos.getEntryOrder().getAvgFillPrice(),
            round((3 * 5.83 + 2 * 5.76) / 5.0, 2)
        )

    def testMinTrade(self):
        class TestStrategy(strategy.BaseStrategy):
            def __init__(self, feed, brk):
                strategy.BaseStrategy.__init__(self, feed, brk)
                self.pos = None

            def onBars(self, bars):
                if not self.pos:
                    self.pos = self.enterLongLimit(INSTRUMENT, 4.99, 1, True)

        barFeed = btcbarfeed.CSVTradeFeed()
        barFeed.addBarsFromCSV(tc_common.get_data_file_path("bitstampUSD.csv"), instrument=INSTRUMENT)
        brk = broker.BacktestingBroker({PRICE_CURRENCY: 100}, barFeed)
        strat = TestStrategy(barFeed, brk)
        with self.assertRaisesRegexp(Exception, "USD amount must be >= 25"):
            strat.run()


class PaperTradingTestCase(tc_common.TestCase):
    def testBuyWithPartialFill(self):

        class Strategy(TestStrategy):
            def __init__(self, feed, brk):
                TestStrategy.__init__(self, feed, brk)
                self.pos = None

            def onBars(self, bars):
                if self.pos is None:
                    self.pos = self.enterLongLimit(INSTRUMENT, 100, 1, True)

        barFeed = TestingLiveTradeFeed()
        barFeed.addTrade(datetime.datetime(2000, 1, 1), 1, 100, 0.1)
        barFeed.addTrade(datetime.datetime(2000, 1, 2), 1, 100, 0.1)
        barFeed.addTrade(datetime.datetime(2000, 1, 2), 1, 101, 10)
        barFeed.addTrade(datetime.datetime(2000, 1, 3), 1, 100, 0.2)

        brk = broker.PaperTradingBroker({PRICE_CURRENCY: 1000}, barFeed)
        strat = Strategy(barFeed, brk)
        strat.run()

        self.assertTrue(strat.pos.isOpen())
        self.assertEqual(round(strat.pos.getShares(), 3), 0.3)
        self.assertEqual(len(strat.posExecutionInfo), 1)
        self.assertEqual(strat.pos.getEntryOrder().getSubmitDateTime().date(), datetime.datetime.now().date())

    def testBuyAndSellWithPartialFill1(self):

        class Strategy(TestStrategy):
            def __init__(self, feed, brk):
                TestStrategy.__init__(self, feed, brk)
                self.pos = None

            def onBars(self, bars):
                if self.pos is None:
                    self.pos = self.enterLongLimit(INSTRUMENT, 100, 1, True)
                elif bars.getDateTime() == dt.as_utc(datetime.datetime(2000, 1, 3)):
                    self.pos.exitLimit(101)

        barFeed = TestingLiveTradeFeed()
        barFeed.addTrade(datetime.datetime(2000, 1, 1), 1, 100, 0.1)
        barFeed.addTrade(datetime.datetime(2000, 1, 2), 1, 100, 0.1)
        barFeed.addTrade(datetime.datetime(2000, 1, 2), 1, 101, 10)
        barFeed.addTrade(datetime.datetime(2000, 1, 3), 1, 100, 0.2)
        barFeed.addTrade(datetime.datetime(2000, 1, 4), 1, 100, 0.2)
        barFeed.addTrade(datetime.datetime(2000, 1, 5), 1, 101, 0.2)

        brk = broker.PaperTradingBroker({PRICE_CURRENCY: 1000}, barFeed)
        strat = Strategy(barFeed, brk)
        strat.run()

        self.assertTrue(strat.pos.isOpen())
        self.assertEqual(round(strat.pos.getShares(), 3), 0.1)
        self.assertEqual(len(strat.posExecutionInfo), 1)
        self.assertEqual(strat.pos.getEntryOrder().getSubmitDateTime().date(), datetime.datetime.now().date())
        self.assertEqual(strat.pos.getExitOrder().getSubmitDateTime().date(), datetime.datetime.now().date())

    def testBuyAndSellWithPartialFill2(self):

        class Strategy(TestStrategy):
            def __init__(self, feed, brk):
                TestStrategy.__init__(self, feed, brk)
                self.pos = None

            def onBars(self, bars):
                if self.pos is None:
                    self.pos = self.enterLongLimit(INSTRUMENT, 100, 1, True)
                elif bars.getDateTime() == dt.as_utc(datetime.datetime(2000, 1, 3)):
                    self.pos.exitLimit(101)

        barFeed = TestingLiveTradeFeed()
        barFeed.addTrade(datetime.datetime(2000, 1, 1), 1, 100, 0.1)
        barFeed.addTrade(datetime.datetime(2000, 1, 2), 1, 100, 0.1)
        barFeed.addTrade(datetime.datetime(2000, 1, 2), 1, 101, 10)
        barFeed.addTrade(datetime.datetime(2000, 1, 3), 1, 100, 0.2)
        barFeed.addTrade(datetime.datetime(2000, 1, 4), 1, 100, 0.2)
        barFeed.addTrade(datetime.datetime(2000, 1, 5), 1, 101, 0.2)
        barFeed.addTrade(datetime.datetime(2000, 1, 6), 1, 102, 5)

        brk = broker.PaperTradingBroker({PRICE_CURRENCY: 1000}, barFeed)
        strat = Strategy(barFeed, brk)
        strat.run()

        self.assertFalse(strat.pos.isOpen())
        self.assertEqual(strat.pos.getShares(), 0)
        self.assertEqual(len(strat.posExecutionInfo), 2)
        self.assertEqual(strat.pos.getEntryOrder().getSubmitDateTime().date(), datetime.datetime.now().date())
        self.assertEqual(strat.pos.getExitOrder().getSubmitDateTime().date(), datetime.datetime.now().date())

    def testRoundingBugWithTrades(self):
        # Unless proper rounding is in place 0.03 - 0.01441376 - 0.01445547 - 0.00113077 == 6.50521303491e-19
        # instead of 0.

        class Strategy(TestStrategy):
            def __init__(self, feed, brk):
                TestStrategy.__init__(self, feed, brk)
                self.pos = None

            def onBars(self, bars):
                if self.pos is None:
                    self.pos = self.enterLongLimit(INSTRUMENT, 1000, 0.03, True)
                elif self.pos.entryFilled() and not self.pos.getExitOrder():
                    self.pos.exitLimit(1000, True)

        barFeed = TestingLiveTradeFeed()
        barFeed.addTrade(datetime.datetime(2000, 1, 1), 1, 1000, 1)
        barFeed.addTrade(datetime.datetime(2000, 1, 2), 1, 1000, 0.03)
        barFeed.addTrade(datetime.datetime(2000, 1, 3), 1, 1000, 0.01441376)
        barFeed.addTrade(datetime.datetime(2000, 1, 4), 1, 1000, 0.01445547)
        barFeed.addTrade(datetime.datetime(2000, 1, 5), 1, 1000, 0.00113077)

        brk = broker.PaperTradingBroker({PRICE_CURRENCY: 1000}, barFeed)
        strat = Strategy(barFeed, brk)
        strat.run()

        self.assertEqual(brk.getBalance(SYMBOL), 0)
        self.assertEqual(strat.pos.getEntryOrder().getAvgFillPrice(), 1000)
        self.assertEqual(strat.pos.getExitOrder().getAvgFillPrice(), 1000)
        self.assertEqual(strat.pos.getEntryOrder().getFilled(), 0.03)
        self.assertEqual(strat.pos.getExitOrder().getFilled(), 0.03)
        self.assertEqual(strat.pos.getEntryOrder().getRemaining(), 0)
        self.assertEqual(strat.pos.getExitOrder().getRemaining(), 0)
        self.assertEqual(strat.pos.getEntryOrder().getSubmitDateTime().date(), datetime.datetime.now().date())
        self.assertEqual(strat.pos.getExitOrder().getSubmitDateTime().date(), datetime.datetime.now().date())

        self.assertFalse(strat.pos.isOpen())
        self.assertEqual(len(strat.posExecutionInfo), 2)
        self.assertEqual(strat.pos.getShares(), 0.0)

    def testInvalidOrders(self):
        barFeed = TestingLiveTradeFeed()
        brk = broker.PaperTradingBroker({PRICE_CURRENCY: 1000}, barFeed)
        with self.assertRaises(Exception):
            brk.createLimitOrder(basebroker.Order.Action.BUY, "none", 1, 1)
        with self.assertRaises(Exception):
            brk.createLimitOrder(basebroker.Order.Action.SELL_SHORT, "none", 1, 1)
        with self.assertRaises(Exception):
            brk.createMarketOrder(basebroker.Order.Action.BUY, "none", 1)
        with self.assertRaises(Exception):
            brk.createStopOrder(basebroker.Order.Action.BUY, "none", 1, 1)
        with self.assertRaises(Exception):
            brk.createStopLimitOrder(basebroker.Order.Action.BUY, "none", 1, 1, 1)

    def testBuyWithoutCash(self):
        tc = self

        class Strategy(TestStrategy):
            def __init__(self, feed, brk):
                TestStrategy.__init__(self, feed, brk)
                self.errors = 0

            def onBars(self, bars):
                with tc.assertRaisesRegexp(Exception, "Not enough USD"):
                    self.limitOrder(INSTRUMENT, 10, 3)
                self.errors += 1

        barFeed = TestingLiveTradeFeed()
        barFeed.addTrade(datetime.datetime(2000, 1, 1), 1, 100, 0.1)
        barFeed.addTrade(datetime.datetime(2000, 1, 2), 1, 100, 0.1)
        barFeed.addTrade(datetime.datetime(2000, 1, 2), 1, 101, 10)
        barFeed.addTrade(datetime.datetime(2000, 1, 3), 1, 100, 0.2)

        brk = broker.PaperTradingBroker({PRICE_CURRENCY: 0}, barFeed)
        strat = Strategy(barFeed, brk)
        strat.run()

        self.assertEqual(strat.errors, 4)
        self.assertEqual(brk.getBalance(SYMBOL), 0)
        self.assertEqual(brk.getBalance(PRICE_CURRENCY), 0)

    def testRanOutOfCash(self):
        tc = self

        class Strategy(TestStrategy):
            def __init__(self, feed, brk):
                TestStrategy.__init__(self, feed, brk)
                self.errors = 0

            def onBars(self, bars):
                # The first order should work, the rest should fail.
                if self.getBroker().getBalance(PRICE_CURRENCY):
                    self.limitOrder(INSTRUMENT, 100, 0.3)
                else:
                    with tc.assertRaisesRegexp(Exception, "Not enough USD"):
                        self.limitOrder(INSTRUMENT, 100, 0.3)
                    self.errors += 1

        barFeed = TestingLiveTradeFeed()
        barFeed.addTrade(datetime.datetime(2000, 1, 1), 1, 100, 10)
        barFeed.addTrade(datetime.datetime(2000, 1, 2), 1, 100, 10)
        barFeed.addTrade(datetime.datetime(2000, 1, 3), 1, 100, 10)

        brk = broker.PaperTradingBroker({PRICE_CURRENCY: 30.15}, barFeed)
        strat = Strategy(barFeed, brk)
        strat.run()

        self.assertEqual(strat.errors, 2)
        self.assertEqual(brk.getBalance(SYMBOL), 0.3)
        self.assertEqual(brk.getBalance(PRICE_CURRENCY), 0)

    def testSellWithoutBTC(self):
        tc = self

        class Strategy(TestStrategy):
            def __init__(self, feed, brk):
                TestStrategy.__init__(self, feed, brk)
                self.errors = 0

            def onBars(self, bars):
                with tc.assertRaisesRegexp(Exception, "Not enough BTC"):
                    self.limitOrder(INSTRUMENT, 100, -0.5)
                self.errors += 1

        barFeed = TestingLiveTradeFeed()
        barFeed.addTrade(datetime.datetime(2000, 1, 1), 1, 100, 10)
        barFeed.addTrade(datetime.datetime(2000, 1, 2), 1, 100, 10)

        brk = broker.PaperTradingBroker({PRICE_CURRENCY: 0}, barFeed)
        strat = Strategy(barFeed, brk)
        strat.run()

        self.assertEqual(strat.errors, 2)
        self.assertEqual(brk.getBalance(SYMBOL), 0)
        self.assertEqual(brk.getBalance(PRICE_CURRENCY), 0)

    def testRanOutOfCoins(self):
        tc = self

        class Strategy(TestStrategy):
            def __init__(self, feed, brk):
                TestStrategy.__init__(self, feed, brk)
                self.errors = 0
                self.bought = False

            def onBars(self, bars):
                if not self.bought:
                    self.limitOrder(INSTRUMENT, 100, 0.5)
                    self.bought = True
                elif self.getBroker().getBalance(SYMBOL) > 0:
                    self.limitOrder(INSTRUMENT, 100, -self.getBroker().getBalance(SYMBOL))
                else:
                    with tc.assertRaisesRegexp(Exception, "Not enough BTC"):
                        self.limitOrder(INSTRUMENT, 100, -1)
                    self.errors += 1

        barFeed = TestingLiveTradeFeed()
        barFeed.addTrade(datetime.datetime(2000, 1, 1), 1, 100, 10)
        barFeed.addTrade(datetime.datetime(2000, 1, 2), 1, 100, 10)
        barFeed.addTrade(datetime.datetime(2000, 1, 3), 1, 100, 10)

        brk = broker.PaperTradingBroker({PRICE_CURRENCY: 50.5}, barFeed)
        strat = Strategy(barFeed, brk)
        strat.run()

        self.assertEqual(strat.errors, 1)
        self.assertEqual(brk.getBalance(SYMBOL), 0)
        self.assertEqual(brk.getBalance(PRICE_CURRENCY), 50)


class LiveTradingTestCase(tc_common.TestCase):
    def testMapUserTransactionsToOrderEvents(self):
        class Strategy(TestStrategy):
            def __init__(self, feed, brk):
                TestStrategy.__init__(self, feed, brk)

            def onBars(self, bars):
                self.stop()

        barFeed = TestingLiveTradeFeed()
        # This is to hit onBars and stop strategy execution.
        barFeed.addTrade(datetime.datetime.now(), 1, 100, 1)

        brk = TestingLiveBroker(None, None, None)
        httpClient = brk.getHTTPClient()
        httpClient.setUSDAvailable(0)
        httpClient.setBTCAvailable(0.1)

        httpClient.addOpenOrder(1, -0.1, 578.79)
        httpClient.addOpenOrder(2, 0.1, 567.21)

        httpClient.addUserTransaction(1, -0.04557395, 26.38, 578.79, 0.14)
        httpClient.addUserTransaction(2, 0.04601436, -26.10, 567.21, 0.14)

        strat = Strategy(barFeed, brk)
        strat.run()

        self.assertEqual(len(strat.orderExecutionInfo), 2)
        self.assertEqual(strat.orderExecutionInfo[0].getPrice(), 578.79)
        self.assertEqual(strat.orderExecutionInfo[0].getQuantity(), 0.04557395)
        self.assertEqual(strat.orderExecutionInfo[0].getCommission(), 0.14)
        self.assertEqual(strat.orderExecutionInfo[0].getDateTime().date(), datetime.datetime.now().date())
        self.assertEqual(strat.orderExecutionInfo[1].getPrice(), 567.21)
        self.assertEqual(strat.orderExecutionInfo[1].getQuantity(), 0.04601436)
        self.assertEqual(strat.orderExecutionInfo[1].getCommission(), 0.14)
        self.assertEqual(strat.orderExecutionInfo[1].getDateTime().date(), datetime.datetime.now().date())

    def testCancelOrder(self):
        class Strategy(TestStrategy):
            def __init__(self, feed, brk):
                super(Strategy, self).__init__(feed, brk)

            def onBars(self, bars):
                order = self.getBroker().getActiveOrders()[0]
                self.getBroker().cancelOrder(order)
                self.stop()

        barFeed = TestingLiveTradeFeed()
        # This is to hit onBars and stop strategy execution.
        barFeed.addTrade(datetime.datetime.now(), 1, 100, 1)

        brk = TestingLiveBroker(None, None, None)
        httpClient = brk.getHTTPClient()
        httpClient.setUSDAvailable(0)
        httpClient.setBTCAvailable(0)
        httpClient.addOpenOrder(1, 0.1, 578.79)

        strat = Strategy(barFeed, brk)
        strat.run()

        self.assertEqual(brk.getBalance(SYMBOL), 0)
        self.assertEqual(brk.getBalance(PRICE_CURRENCY), 0)
        self.assertEqual(len(strat.orderExecutionInfo), 1)
        self.assertEqual(strat.orderExecutionInfo[0], None)
        self.assertEqual(len(strat.ordersUpdated), 1)
        self.assertTrue(strat.ordersUpdated[0].isCanceled())

    def testBuyAndSell(self):
        class Strategy(TestStrategy):
            def __init__(self, feed, brk):
                super(Strategy, self).__init__(feed, brk)
                self.buyOrder = None
                self.sellOrder = None

            def onOrderUpdated(self, orderEvent):
                super(Strategy, self).onOrderUpdated(orderEvent)
                order = orderEvent.getOrder()

                if order == self.buyOrder and order.isPartiallyFilled():
                    if self.sellOrder is None:
                        self.sellOrder = self.limitOrder(INSTRUMENT, 10, -0.5)
                        brk.getHTTPClient().addUserTransaction(self.sellOrder.getId(), -0.5, 5, 10, 0.01)
                elif order == self.sellOrder and order.isFilled():
                    self.stop()

            def onBars(self, bars):
                if self.buyOrder is None:
                    self.buyOrder = self.limitOrder(INSTRUMENT, 10, 1)
                    brk.getHTTPClient().addUserTransaction(self.buyOrder.getId(), 0.5, -5, 10, 0.01)

        barFeed = TestingLiveTradeFeed()
        # This is to get onBars called once.
        barFeed.addTrade(datetime.datetime.now(), 1, 100, 1)

        brk = TestingLiveBroker(None, None, None)
        httpClient = brk.getHTTPClient()
        httpClient.setUSDAvailable(10)
        httpClient.setBTCAvailable(0)

        strat = Strategy(barFeed, brk)
        strat.run()

        self.assertTrue(strat.buyOrder.isPartiallyFilled())
        self.assertTrue(strat.sellOrder.isFilled())
        # 2 events for each order: 1 for accepted, 1 for fill.
        self.assertEqual(len(strat.orderExecutionInfo), 4)
        self.assertEqual(strat.orderExecutionInfo[0], None)
        self.assertEqual(strat.orderExecutionInfo[1].getPrice(), 10)
        self.assertEqual(strat.orderExecutionInfo[1].getQuantity(), 0.5)
        self.assertEqual(strat.orderExecutionInfo[1].getCommission(), 0.01)
        self.assertEqual(strat.orderExecutionInfo[1].getDateTime().date(), datetime.datetime.now().date())
        self.assertEqual(strat.orderExecutionInfo[2], None)
        self.assertEqual(strat.orderExecutionInfo[3].getPrice(), 10)
        self.assertEqual(strat.orderExecutionInfo[3].getQuantity(), 0.5)
        self.assertEqual(strat.orderExecutionInfo[3].getCommission(), 0.01)
        self.assertEqual(strat.orderExecutionInfo[3].getDateTime().date(), datetime.datetime.now().date())


class WebSocketTestCase(tc_common.TestCase):
    def testBarFeed(self):
        events = {
            "on_bars": False,
            "on_order_book_updated": False,
            "break": False,
            "start": datetime.datetime.now()
        }

        disp = dispatcher.Dispatcher()
        barFeed = barfeed.LiveTradeFeed([INSTRUMENT])
        disp.addSubject(barFeed)

        def on_bars(dateTime, bars):
            bars[INSTRUMENT]
            events["on_bars"] = True
            if events["on_order_book_updated"] is True:
                disp.stop()

        def on_order_book_updated(orderBookUpdate):
            events["on_order_book_updated"] = True
            if events["on_bars"] is True:
                disp.stop()

        def on_idle():
            # Stop after 5 minutes.
            if (datetime.datetime.now() - events["start"]).seconds > 60*5:
                disp.stop()

        # Subscribe to events.
        barFeed.getNewValuesEvent().subscribe(on_bars)
        barFeed.getOrderBookUpdateEvent().subscribe(on_order_book_updated)
        disp.getIdleEvent().subscribe(on_idle)
        disp.run()

        # Check that we received both events.
        self.assertTrue(events["on_bars"])
        self.assertTrue(events["on_order_book_updated"])

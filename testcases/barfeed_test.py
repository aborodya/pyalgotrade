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

import datetime

from . import common

from pyalgotrade import barfeed
from pyalgotrade.barfeed import common as bfcommon
from pyalgotrade import bar
from pyalgotrade import dispatcher


QUOTE_SYMBOL = "ORCL"
PRICE_CURRENCY = "USD"
INSTRUMENT = "%s/%s" % (QUOTE_SYMBOL, PRICE_CURRENCY)


def check_base_barfeed(testCase, barFeed, barsHaveAdjClose):
    called = {"called": True}

    def callback(dateTime, bars):
        called["called"] = True
        testCase.assertEqual(barFeed.getCurrentDateTime(), dateTime)

    testCase.assertEqual(barFeed.getCurrentDateTime(), None)
    testCase.assertEqual(barFeed.barsHaveAdjClose(), barsHaveAdjClose)
    if not barsHaveAdjClose:
        with testCase.assertRaisesRegexp(Exception, "The barfeed doesn't support adjusted close values.*"):
            barFeed.setUseAdjustedValues(True)

    d = dispatcher.Dispatcher()
    d.addSubject(barFeed)
    barFeed.getNewValuesEvent().subscribe(callback)
    d.run()

    testCase.assertEqual(called["called"], True)


class OptimizerBarFeedTestCase(common.TestCase):
    def testDateTimesNotInOrder(self):
        bars = [
            bar.Bars([
                bar.BasicBar(
                    INSTRUMENT, datetime.datetime(2001, 1, 2), 1, 1, 1, 1, 1, 1, bar.Frequency.DAY
                )
            ]),
            bar.Bars([
                bar.BasicBar(
                    INSTRUMENT, datetime.datetime(2001, 1, 1), 1, 1, 1, 1, 1, 1, bar.Frequency.DAY
                )
            ]),
        ]
        f = barfeed.OptimizerBarFeed(bar.Frequency.DAY, [INSTRUMENT], bars)
        with self.assertRaisesRegexp(Exception, "%s bars are not in order.*" % INSTRUMENT):
            for dt, b in f:
                pass

    def testDupliateDateTimesForDailyBars(self):
        bars = [
            bar.Bars([
                bar.BasicBar(
                    INSTRUMENT, datetime.datetime(2001, 1, 1), 1, 1, 1, 1, 1, 1, bar.Frequency.DAY
                )
            ]),
            bar.Bars([
                bar.BasicBar(
                    INSTRUMENT, datetime.datetime(2001, 1, 1), 1, 1, 1, 1, 1, 1, bar.Frequency.DAY
                )
            ]),
        ]
        f = barfeed.OptimizerBarFeed(bar.Frequency.DAY, [INSTRUMENT], bars)
        with self.assertRaisesRegexp(Exception, "%s bars are not in order.*" % INSTRUMENT):
            for dt, b in f:
                pass

    def testDupliateDateTimesForTradeBars(self):
        bars = [
            bar.Bars([
                bar.BasicBar(
                    INSTRUMENT, datetime.datetime(2001, 1, 1), 1, 1, 1, 1, 1, 1, bar.Frequency.TRADE
                )
            ]),
            bar.Bars([
                bar.BasicBar(
                    INSTRUMENT, datetime.datetime(2001, 1, 1), 1, 1, 1, 1, 2, 1, bar.Frequency.TRADE
                )
            ]),
        ]
        f = barfeed.OptimizerBarFeed(bar.Frequency.TRADE, [INSTRUMENT], bars)
        expected_volume = 1
        for dt, b in f:
            assert b[INSTRUMENT].getVolume() == expected_volume
            expected_volume += 1

    def testBaseBarFeed(self):
        bars = [
            bar.Bars([
                bar.BasicBar(
                    INSTRUMENT, datetime.datetime(2001, 1, 1), 1, 1, 1, 1, 1, 1, bar.Frequency.DAY
                )
            ]),
            bar.Bars([
                bar.BasicBar(
                    INSTRUMENT, datetime.datetime(2001, 1, 2), 1, 1, 1, 1, 1, 1, bar.Frequency.DAY
                )
            ]),
        ]
        barFeed = barfeed.OptimizerBarFeed(bar.Frequency.DAY, [INSTRUMENT], bars)
        check_base_barfeed(self, barFeed, True)

    def testBaseBarFeedNoAdjClose(self):
        bars = [
            bar.Bars([
                bar.BasicBar(
                    INSTRUMENT, datetime.datetime(2001, 1, 1), 1, 1, 1, 1, 1, None, bar.Frequency.DAY
                )
            ]),
            bar.Bars([
                bar.BasicBar(
                    INSTRUMENT, datetime.datetime(2001, 1, 2), 1, 1, 1, 1, 1, None, bar.Frequency.DAY
                )
            ]),
        ]
        barFeed = barfeed.OptimizerBarFeed(bar.Frequency.DAY, [INSTRUMENT], bars)
        check_base_barfeed(self, barFeed, False)

    def testEmtpy(self):
        barFeed = barfeed.OptimizerBarFeed(bar.Frequency.DAY, [INSTRUMENT], [])
        self.assertEqual(barFeed.barsHaveAdjClose(), False)


class CommonTestCase(common.TestCase):
    def testSanitize(self):
        self.assertEqual(bfcommon.sanitize_ohlc(10, 12, 9, 10), (10, 12, 9, 10))
        self.assertEqual(bfcommon.sanitize_ohlc(10, 12, 9, 13), (10, 13, 9, 13))
        self.assertEqual(bfcommon.sanitize_ohlc(10, 9, 9, 10), (10, 10, 9, 10))
        self.assertEqual(bfcommon.sanitize_ohlc(10, 12, 11, 10), (10, 12, 10, 10))
        self.assertEqual(bfcommon.sanitize_ohlc(10, 12, 10, 9), (10, 12, 9, 9))

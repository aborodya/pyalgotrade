from __future__ import print_function

from pyalgotrade import strategy
from pyalgotrade.tools import quandl
from pyalgotrade.technical import vwap
from pyalgotrade.stratanalyzer import sharpe


class VWAPMomentum(strategy.BacktestingStrategy):
    def __init__(self, feed, instrument, initialBalance, vwapWindowSize, threshold):
        super(VWAPMomentum, self).__init__(feed, balances=initialBalance)
        self.__instrument = instrument
        self.__vwap = vwap.VWAP(feed.getDataSeries(instrument), vwapWindowSize)
        self.__threshold = threshold

    def getVWAP(self):
        return self.__vwap

    def onBars(self, bars):
        vwap = self.__vwap[-1]
        if vwap is None:
            return

        shares = self.getBroker().getBalance(self.__instrument.split("/")[0])
        price = bars.getBar(self.__instrument).getClose()
        notional = shares * price

        if price > vwap * (1 + self.__threshold) and notional < 1000000:
            self.marketOrder(self.__instrument, 100)
        elif price < vwap * (1 - self.__threshold) and notional > 0:
            self.marketOrder(self.__instrument, -100)


def main(plot):
    symbol = "AAPL"
    priceCurrency = "USD"
    instrument = "%s/%s" % (symbol, priceCurrency)
    initialBalance = {priceCurrency: 1000000}
    vwapWindowSize = 5
    threshold = 0.01

    # Download the bars.
    feed = quandl.build_feed("WIKI", [symbol], priceCurrency, 2011, 2012, ".")

    strat = VWAPMomentum(feed, instrument, initialBalance, vwapWindowSize, threshold)
    sharpeRatioAnalyzer = sharpe.SharpeRatio(priceCurrency)
    strat.attachAnalyzer(sharpeRatioAnalyzer)

    if plot:
        from pyalgotrade import plotter

        plt = plotter.StrategyPlotter(strat, True, False, True)
        plt.getInstrumentSubplot(instrument).addDataSeries("vwap", strat.getVWAP())

    strat.run()
    print("Sharpe ratio: %.2f" % sharpeRatioAnalyzer.getSharpeRatio(0.05))

    if plot:
        plt.plot()


if __name__ == "__main__":
    main(True)

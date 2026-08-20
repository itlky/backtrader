#!/usr/bin/env python3
"""
Run a multi-data backtest, record portfolio timeseries and trades, save CSVs,
and optionally serve a Dash app to visualize the equity curve and trade markers.

Usage:
  python backtest_and_dash.py --data0 ../../datas/orcl-2003-2005.txt \
                             --data1 ../../datas/yhoo-2003-2005.txt \
                             --fromdate 2003-01-01 --todate 2005-12-31 \
                             --dash
"""
from __future__ import annotations
import argparse
import datetime
import pandas as pd
import backtrader as bt
import backtrader.feeds as btfeeds
import backtrader.indicators as btind
import plotly.graph_objects as go
from dash import Dash, dcc, html

class RecordingStrategy(bt.Strategy):
    params = dict(period=15, stake=10, printout=False)

    def __init__(self):
        # state
        self.orderid = None
        self.portfolio = []  # list of dicts: datetime, value, cash, position
        self.trades = []     # list of dicts: datetime, type, price, size

        # indicators on data1 (second data)
        sma = btind.MovAv.SMA(self.data1, period=self.p.period)
        self.signal = btind.CrossOver(self.data1.close, sma)

    def log(self, txt, dt=None):
        if self.p.printout:
            dt = dt or self.data.datetime[0]
            print(f"{bt.num2date(dt).isoformat()}, {txt}")

    def notify_order(self, order):
        # ignore submitted/accepted
        if order.status in [bt.Order.Submitted, bt.Order.Accepted]:
            return

        # completed
        if order.status == order.Completed:
            dt = order.executed.dt if hasattr(order.executed, "dt") else self.data.datetime[0]
            dt = bt.num2date(dt)
            price = order.executed.price
            size = order.executed.size
            if order.isbuy():
                self.trades.append({"datetime": dt, "type": "BUY", "price": price, "size": size})
                self.log(f"BUY EXECUTED {price:.2f} size={size}", dt)
            else:
                self.trades.append({"datetime": dt, "type": "SELL", "price": price, "size": size})
                self.log(f"SELL EXECUTED {price:.2f} size={size}", dt)
        elif order.status in [order.Expired, order.Canceled, order.Margin]:
            # could log if desired
            pass

        # allow new orders
        self.orderid = None

    def next(self):
        # record portfolio snapshot on each bar (use data0 datetime for timeline)
        dt = bt.num2date(self.data0.datetime[0])
        val = self.broker.getvalue()
        cash = self.broker.getcash()
        pos_size = 0
        # position on data0 (we place orders on first data)
        if len(self.datas[0].lines) > 0:
            pos = self.getposition(self.datas[0])
            pos_size = pos.size if pos else 0
        self.portfolio.append({"datetime": dt, "value": val, "cash": cash, "position": pos_size})

        # basic trading logic (same as example): buy/sell based on signal on data1
        if self.orderid:
            return

        if not self.position:  # not in market
            if self.signal > 0:
                # buy on data0
                self.log(f"BUY CREATE {self.data1.close[0]:.2f}", dt)
                o = self.buy(size=self.p.stake)
                self.orderid = o.ref if hasattr(o, "ref") else None
        else:
            if self.signal < 0:
                self.log(f"SELL CREATE {self.data1.close[0]:.2f}", dt)
                o = self.sell(size=self.p.stake)
                self.orderid = o.ref if hasattr(o, "ref") else None

def run_backtest(args):
    cerebro = bt.Cerebro()
    # read dates
    fromdate = datetime.datetime.strptime(args.fromdate, "%Y-%m-%d")
    todate = datetime.datetime.strptime(args.todate, "%Y-%m-%d")

    # add data0
    data0 = btfeeds.YahooFinanceCSVData(dataname=args.data0, fromdate=fromdate, todate=todate)
    cerebro.adddata(data0)

    # data1
    data1 = btfeeds.YahooFinanceCSVData(dataname=args.data1, fromdate=fromdate, todate=todate)
    cerebro.adddata(data1)

    cerebro.addstrategy(RecordingStrategy, period=args.period, stake=args.stake, printout=args.printout)

    cerebro.broker.setcash(args.cash)
    cerebro.broker.setcommission(commission=args.commperc)

    results = cerebro.run(runonce=not args.runnext, preload=not args.nopreload, oldsync=args.oldsync)
    strat = results[0]

    # Build DataFrames
    port_df = pd.DataFrame(strat.portfolio)
    if not port_df.empty:
        port_df = port_df.set_index(pd.DatetimeIndex(port_df["datetime"])) .drop(columns=["datetime"]).sort_index()

    trades_df = pd.DataFrame(strat.trades)
    if not trades_df.empty:
        # ensure datetimes
        trades_df["datetime"] = pd.to_datetime(trades_df["datetime"])
        trades_df = trades_df.sort_values("datetime").reset_index(drop=True)

    # Save CSVs
    port_df.to_csv("portfolio_timeseries.csv")
    trades_df.to_csv("trades.csv", index=False)
    print("Saved portfolio_timeseries.csv and trades.csv")

    return port_df, trades_df

def make_dash_app(port_df: pd.DataFrame, trades_df: pd.DataFrame):
    app = Dash(__name__)
    fig = go.Figure()
    # equity curve
    fig.add_trace(go.Scatter(x=port_df.index, y=port_df["value"], mode="lines", name="Equity"))
    # plot buys/sells
    if not trades_df.empty:
        buys = trades_df[trades_df["type"] == "BUY"]
        sells = trades_df[trades_df["type"] == "SELL"]
        if not buys.empty:
            fig.add_trace(go.Scatter(x=buys["datetime"], y=buys["price"], mode="markers", name="Buys",
                                     marker=dict(color="green", size=8, symbol="triangle-up")))
        if not sells.empty:
            fig.add_trace(go.Scatter(x=sells["datetime"], y=sells["price"], mode="markers", name="Sells",
                                     marker=dict(color="red", size=8, symbol="triangle-down")))

    fig.update_layout(title="Backtest Equity Curve & Trades", xaxis_title="Date", yaxis_title="Portfolio Value / Trade Price")

    # build layout
    app.layout = html.Div(children=[
        html.H1(children="Backtest Dashboard"),
        dcc.Graph(id="equity-graph", figure=fig),
        html.Div(children=[
            html.H3("Portfolio snapshot (last rows)"),
            dcc.Markdown(port_df.tail().to_html())
        ], style={"marginTop": 20})
    ])
    return app

def parse_args():
    parser = argparse.ArgumentParser(description="Run backtest and optionally serve Dash")
    parser.add_argument("--data0", "-d0", default="../../datas/orcl-2003-2005.txt", help="1st data CSV")
    parser.add_argument("--data1", "-d1", default="../../datas/yhoo-2003-2005.txt", help="2nd data CSV")
    parser.add_argument("--fromdate", "-f", default="2003-01-01")
    parser.add_argument("--todate", "-t", default="2005-12-31")
    parser.add_argument("--period", type=int, default=15)
    parser.add_argument("--cash", type=float, default=100000)
    parser.add_argument("--commperc", type=float, default=0.005)
    parser.add_argument("--stake", type=int, default=10)
    parser.add_argument("--runnext", action="store_true")
    parser.add_argument("--nopreload", action="store_true")
    parser.add_argument("--oldsync", action="store_true")
    parser.add_argument("--printout", action="store_true")
    parser.add_argument("--dash", action="store_true", help="Start Dash server after running backtest")
    return parser.parse_args()

if __name__ == "__main__":
    args = parse_args()
    port_df, trades_df = run_backtest(args)
    if args.dash:
        app = make_dash_app(port_df, trades_df)
        # by default run on localhost:8050
        app.run_server(debug=True)
    else:
        print("Run with --dash to start the interactive dashboard.")

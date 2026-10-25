#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include <pybind11/numpy.h>
#include <vector>
#include <cmath>
#include <algorithm>
#include <numeric>
#include <random>
#include <span>
#include <execution>

namespace py = pybind11;

// ── OHLCV Bar ─────────────────────────────────────────────────────────
struct Bar {
    double open, high, low, close, volume;
};

// ── Trade Record ──────────────────────────────────────────────────────
struct Trade {
    int bar_index;
    double price;
    double qty;
    double commission;
    std::string side; // "buy" or "sell"
};

// ── Backtest Result ───────────────────────────────────────────────────
struct BacktestResult {
    std::vector<double> equity_curve;
    std::vector<double> drawdown_curve;
    double total_return;
    double annualized_return;
    double volatility;
    double sharpe_ratio;
    double sortino_ratio;
    double max_drawdown;
    double calmar_ratio;
    double win_rate;
    int total_trades;
    double profit_factor;
};

// ── Vectorized Backtester ─────────────────────────────────────────────
class VectorizedBacktester {
public:
    double initial_capital;
    double commission_pct;
    double slippage_pct;

    VectorizedBacktester(double capital = 100000.0, double comm = 0.001, double slip = 0.0005)
        : initial_capital(capital), commission_pct(comm), slippage_pct(slip) {}

    // Signal-based backtest: signals[-1,1], prices, returns
    BacktestResult run_signal_backtest(
        const std::vector<double>& signals,
        const std::vector<Bar>& bars,
        double position_size_pct = 0.25,
        double stop_loss = -0.05,
        double take_profit = 0.12,
        int min_hold = 2)
    {
        BacktestResult result{};
        int n = std::min(signals.size(), bars.size());
        if (n < 10) return result;

        std::vector<double> equity(n, initial_capital);
        std::vector<double> peak(n, initial_capital);

        double position = 0.0;
        double entry_price = 0.0;
        int hold_count = 0;
        int wins = 0, losses = 0;
        double gross_profit = 0.0, gross_loss = 0.0;
        int trades = 0;

        for (int i = 1; i < n; ++i) {
            double price = bars[i].close;
            double prev_price = bars[i-1].close;
            double ret = (price / prev_price) - 1.0;

            // Check stop/take profit
            if (position != 0.0 && hold_count >= min_hold) {
                double pnl_pct = (price - entry_price) / entry_price;
                if (pnl_pct < stop_loss || pnl_pct > take_profit) {
                    double exit_ret = (position > 0) ? pnl_pct : -pnl_pct;
                    double slippage = slippage_pct * std::abs(position * price);
                    double comm = commission_pct * std::abs(position * price);
                    equity[i] = equity[i-1] * (1.0 + exit_ret) - slippage - comm;

                    if (exit_ret > 0) { wins++; gross_profit += exit_ret; }
                    else { losses++; gross_loss += exit_ret; }
                    trades++;

                    position = 0.0;
                    entry_price = 0.0;
                    hold_count = 0;
                    continue;
                }
            }

            // Signal-based position sizing
            if (position == 0.0 && i > 0) {
                double signal = signals[i-1];
                if (std::abs(signal) > 0.15) {
                    double capped_signal = std::max(-1.0, std::min(1.0, signal));
                    double size_fraction = position_size_pct * std::abs(capped_signal);
                    position = (capped_signal > 0 ? 1.0 : -1.0) * size_fraction * equity[i-1] / price;
                    entry_price = price;
                    hold_count = 0;

                    double comm = commission_pct * std::abs(position * price);
                    equity[i] = equity[i-1] - comm;
                    continue;
                }
            }

            // Carry position
            if (position != 0.0) {
                double pnl_pct = (position > 0) ? ret : -ret;
                equity[i] = equity[i-1] * (1.0 + pnl_pct);
                hold_count++;
            } else {
                equity[i] = equity[i-1];
            }

            peak[i] = std::max(peak[i-1], equity[i]);
        }

        // Fill result
        double total_ret = (equity.back() - initial_capital) / initial_capital;
        double trading_days = static_cast<double>(n);

        result.equity_curve = equity;
        result.drawdown_curve.resize(n);
        for (int i = 0; i < n; ++i) {
            double dd = (peak[i] > 0) ? (peak[i] - equity[i]) / peak[i] : 0.0;
            result.drawdown_curve[i] = dd;
        }

        result.total_return = total_ret;
        result.annualized_return = std::pow(1.0 + total_ret, 252.0 / trading_days) - 1.0;
        result.max_drawdown = *std::max_element(result.drawdown_curve.begin(), result.drawdown_curve.end());

        // Daily returns
        std::vector<double> daily_ret(n - 1);
        for (int i = 1; i < n; ++i) {
            daily_ret[i-1] = (equity[i] / equity[i-1]) - 1.0;
        }

        double mean_ret = std::accumulate(daily_ret.begin(), daily_ret.end(), 0.0) / daily_ret.size();
        double sq_sum = 0.0;
        for (double r : daily_ret) sq_sum += (r - mean_ret) * (r - mean_ret);
        double std_ret = std::sqrt(sq_sum / daily_ret.size());

        result.volatility = std_ret * std::sqrt(252.0);
        result.sharpe_ratio = (mean_ret / std::max(std_ret, 1e-10)) * std::sqrt(252.0);

        double downside_sum = 0.0;
        int downside_count = 0;
        for (double r : daily_ret) {
            if (r < 0) { downside_sum += r * r; downside_count++; }
        }
        double downside_std = std::sqrt(downside_sum / std::max(downside_count, 1));
        result.sortino_ratio = (mean_ret / std::max(downside_std, 1e-10)) * std::sqrt(252.0);

        result.calmar_ratio = result.annualized_return / std::max(result.max_drawdown, 1e-10);
        result.total_trades = trades;
        result.win_rate = (trades > 0) ? static_cast<double>(wins) / trades : 0.0;
        result.profit_factor = (std::abs(gross_loss) > 1e-10) ? gross_profit / std::abs(gross_loss) : 0.0;

        return result;
    }

    // Walk-forward backtest with multiple windows
    BacktestResult run_walkforward(
        const std::vector<double>& signals,
        const std::vector<Bar>& bars,
        int train_window = 504,   // 2 years
        int test_window = 63,     // 3 months
        double position_size = 0.25)
    {
        int n = std::min(signals.size(), bars.size());
        BacktestResult result{};
        if (n < train_window + test_window) return result;

        std::vector<double> all_signals(n, 0.0);

        for (int start = 0; start + train_window + test_window <= n; start += test_window) {
            int train_end = start + train_window;
            int test_end = std::min(train_end + test_window, n);

            std::vector<double> train_signals(signals.begin() + start, signals.begin() + train_end);
            std::vector<Bar> train_bars(bars.begin() + start, bars.begin() + train_end);

            // In walkforward, we just use signals as-is (model would be retrained per window)
            for (int i = train_end; i < test_end && i < n; ++i) {
                all_signals[i] = signals[i];
            }
        }

        return run_signal_backtest(all_signals, bars, position_size);
    }
};

// ── Pybind11 ──────────────────────────────────────────────────────────

PYBIND11_MODULE(nexus_backtester, m) {
    m.doc() = "Nexus Vectorized C++ Backtesting Engine";

    py::class_<Bar>(m, "Bar")
        .def(py::init<>())
        .def_readwrite("open", &Bar::open)
        .def_readwrite("high", &Bar::high)
        .def_readwrite("low", &Bar::low)
        .def_readwrite("close", &Bar::close)
        .def_readwrite("volume", &Bar::volume);

    py::class_<BacktestResult>(m, "BacktestResult")
        .def_readonly("equity_curve", &BacktestResult::equity_curve)
        .def_readonly("drawdown_curve", &BacktestResult::drawdown_curve)
        .def_readonly("total_return", &BacktestResult::total_return)
        .def_readonly("annualized_return", &BacktestResult::annualized_return)
        .def_readonly("volatility", &BacktestResult::volatility)
        .def_readonly("sharpe_ratio", &BacktestResult::sharpe_ratio)
        .def_readonly("sortino_ratio", &BacktestResult::sortino_ratio)
        .def_readonly("max_drawdown", &BacktestResult::max_drawdown)
        .def_readonly("calmar_ratio", &BacktestResult::calmar_ratio)
        .def_readonly("win_rate", &BacktestResult::win_rate)
        .def_readonly("total_trades", &BacktestResult::total_trades)
        .def_readonly("profit_factor", &BacktestResult::profit_factor)
        .def("__repr__", [](const BacktestResult& r) {
            return "<BacktestResult Sharpe=" + std::to_string(r.sharpe_ratio) +
                   " Ret=" + std::to_string(r.total_return * 100) + "%" +
                   " MDD=" + std::to_string(r.max_drawdown * 100) + "%" +
                   " Trades=" + std::to_string(r.total_trades) + ">";
        });

    py::class_<VectorizedBacktester>(m, "VectorizedBacktester")
        .def(py::init<double, double, double>(),
             py::arg("initial_capital") = 100000.0,
             py::arg("commission_pct") = 0.001,
             py::arg("slippage_pct") = 0.0005)
        .def("run_signal_backtest", &VectorizedBacktester::run_signal_backtest,
             py::arg("signals"), py::arg("bars"),
             py::arg("position_size_pct") = 0.25,
             py::arg("stop_loss") = -0.05,
             py::arg("take_profit") = 0.12,
             py::arg("min_hold") = 2)
        .def("run_walkforward", &VectorizedBacktester::run_walkforward,
             py::arg("signals"), py::arg("bars"),
             py::arg("train_window") = 504,
             py::arg("test_window") = 63,
             py::arg("position_size") = 0.25);
}